"""hs_code 테이블에 있지만 Chroma(vector DB)의 hs_code 컬렉션에 아직 없는 HS 코드만 임베딩해 추가합니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.hs_code_embed [--rebuild]
스케줄: backend/app/scheduler/main.py가 매일 01:00(KST)에 실행합니다. 연간 HS 갱신(00:15)이나 실적 적재가
hs_code에 코드를 추가하면 그날 밤 임베딩됩니다. 새 코드가 없으면 OpenAI를 부르지 않습니다.

- 문서: 코드와 상위 분류명 경로를 함께 넣습니다. 10단위 '기타'처럼 이름만으로는 뜻이 없는 코드도 검색되게 하기 위해서입니다.
    [HS2022] 1509201000 (10단위)
    류 15: 동물성ㆍ식물성ㆍ미생물성 지방과 기름 …
    호 1509: 올리브유와 그 분획물 …
    소호 150920: 엑스트라 버진 올리브유 (원문: 엑스트라 버진 올리브유)
    품목 1509201000: …
- id는 '<hs_ver>:<hs_code>', 메타데이터는 hs_code·hs_ver·depth·상위 코드(hs2·hs4·hs6)·name_ko입니다.
- 컬렉션에 이미 있는 id는 품목명·상위 이름·출처가 바뀌어도 다시 임베딩하지 않습니다 (미등록 id만 추가).
  실패한 코드는 컬렉션에 들어가지 않으므로 다음 실행에서 다시 처리됩니다.
- 임베딩 모델: text-embedding-3-large (한국어 품목명 검색 품질이 OpenAI 임베딩 중 가장 좋음, 3,072차원).
  모델을 바꾸면 차원이 달라지므로 --rebuild로 컬렉션을 새로 만듭니다.
- 저장 위치: 저장소 루트 chroma_data/ (git 제외). 검색은 search()를 씁니다.
"""
import argparse
import logging
from datetime import datetime
from pathlib import Path

import chromadb
from dotenv import dotenv_values
from openai import OpenAI
from sqlalchemy import select

from backend.app.db.session import ENV_PATH, make_engine, session_scope
from backend.app.models.hs_code import HsCodeMst
from backend.app.scheduler.jobs.hs_code_load import hb

MODEL = "text-embedding-3-large"
COLLECTION = "hs_code"
CHROMA_PATH = Path(__file__).resolve().parents[4] / "chroma_data"
LOG_DIR = hb.OUTPUT_DIR / "logs"
BATCH = 500  # OpenAI 임베딩 요청·Chroma add 한 번에 보내는 문서 수
LEVELS = {2: "류", 4: "호", 6: "소호", 10: "품목"}
log = logging.getLogger("hs_code_embed")


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"hs_code_embed_{datetime.now():%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


def read_rows(engine):
    with session_scope(engine) as s:
        cols = (HsCodeMst.hs_code, HsCodeMst.hs_ver, HsCodeMst.name_ko, HsCodeMst.original_name_ko)
        return [dict(zip(("hs_code", "hs_ver", "name_ko", "original_name_ko"), r))
                for r in s.execute(select(*cols)).all()]


def build_docs(rows):
    """{id: (문서, 메타데이터)}. 상위 코드는 같은 판(hs_ver)에서 찾고, 없으면 그 줄을 뺍니다."""
    by_key = {(r["hs_ver"], r["hs_code"]): r for r in rows}
    docs = {}
    for r in rows:
        code, ver = r["hs_code"], r["hs_ver"]
        lines = [f"[{ver}] {code} ({len(code)}단위)"]
        for n in (2, 4, 6, 10):
            p = by_key.get((ver, code[:n])) if n <= len(code) else None
            if p:
                orig = f" (원문: {p['original_name_ko']})" if p["original_name_ko"] else ""
                lines.append(f"{LEVELS[n]} {p['hs_code']}: {p['name_ko']}{orig}")
        doc = "\n".join(lines)
        meta = {"hs_code": code, "hs_ver": ver, "depth": len(code), "name_ko": r["name_ko"]}
        meta |= {f"hs{n}": code[:n] for n in (2, 4, 6) if n < len(code)}
        docs[f"{ver}:{code}"] = (doc, meta)
    return docs


def openai_embed(env=ENV_PATH):
    key = {k.lower(): v for k, v in dotenv_values(env).items()}.get("openai_api_key", "").strip()
    if not key:
        raise ValueError(f"{env}에 OPENAI_API_KEY가 없습니다.")
    client = OpenAI(api_key=key)
    return lambda texts: [d.embedding for d in client.embeddings.create(model=MODEL, input=texts).data]


def get_collection(client, rebuild=False):
    if rebuild and COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
        log.info(f"컬렉션 {COLLECTION}을 지우고 새로 만듭니다 (--rebuild).")
    col = client.get_or_create_collection(COLLECTION, embedding_function=None,
                                          metadata={"hnsw:space": "cosine", "embedding_model": MODEL})
    if (col.metadata or {}).get("embedding_model") != MODEL:
        raise ValueError(f"컬렉션 {COLLECTION}의 임베딩 모델이 {(col.metadata or {}).get('embedding_model')}입니다 "
                         f"(현재 {MODEL}). 차원이 달라 --rebuild로 다시 만들어야 합니다.")
    return col


def sync(rows, col, embed):
    """DB에 있지만 컬렉션에 없는 id만 임베딩해 추가합니다. {"added": 추가, "existing": 이미 있음}"""
    docs = build_docs(rows)
    have = set(col.get(include=[])["ids"])
    todo = [i for i in docs if i not in have]
    for i in range(0, len(todo), BATCH):
        ids = todo[i:i + BATCH]
        texts = [docs[x][0] for x in ids]
        col.add(ids=ids, documents=texts, metadatas=[docs[x][1] for x in ids], embeddings=embed(texts))
        log.info(f"임베딩 {min(i + BATCH, len(todo)):,}/{len(todo):,}")
    return {"added": len(todo), "existing": len(docs) - len(todo)}


def search(query, n=5, hs_ver=None, depth=None, client=None, embed=None):
    """질의와 가까운 HS 코드 [(id, 거리, 메타데이터)]. hs_ver·depth로 좁힐 수 있습니다."""
    col = get_collection(client or chromadb.PersistentClient(path=str(CHROMA_PATH)))
    where = [{k: v} for k, v in (("hs_ver", hs_ver), ("depth", depth)) if v is not None]
    res = col.query(query_embeddings=(embed or openai_embed())([query]), n_results=n,
                    where=(where[0] if len(where) == 1 else {"$and": where}) if where else None,
                    include=["metadatas", "distances"])
    return list(zip(res["ids"][0], res["distances"][0], res["metadatas"][0]))


def run(db_url=None, rebuild=False, client=None, embed=None, log_dir=LOG_DIR):
    """성공 0, 실패 1. client·embed는 테스트에서 바꿔 넣습니다."""
    log_path = setup_log(log_dir)
    log.info(f"HS 코드 임베딩(미등록 id 추가) 시작 | 모델 {MODEL} | Chroma {CHROMA_PATH if client is None else '(주입)'}")
    engine = None
    try:
        engine = make_engine(db_url)
        rows = read_rows(engine)
        col = get_collection(client or chromadb.PersistentClient(path=str(CHROMA_PATH)), rebuild)
        result = sync(rows, col, embed or openai_embed())
    except Exception:
        log.exception(f"실패: 임베딩 중 오류 (이미 추가한 배치는 남고 다음 실행에서 나머지를 추가함) | 로그: {log_path}")
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    log.info(f"완료: DB {len(rows):,}행 | 추가 {result['added']:,} · 이미 있음 {result['existing']:,} | "
             f"컬렉션 {col.count():,}개 | 로그: {log_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="hs_code 테이블의 미등록 코드를 임베딩해 Chroma hs_code 컬렉션에 추가")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    parser.add_argument("--rebuild", action="store_true", help="컬렉션을 지우고 전부 다시 임베딩 (모델 변경 시)")
    args = parser.parse_args()
    raise SystemExit(run(args.db_url, args.rebuild))


if __name__ == "__main__":
    main()
