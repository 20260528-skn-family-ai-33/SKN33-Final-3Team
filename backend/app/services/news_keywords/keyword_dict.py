"""키워드 사전을 DB(keyword_category·keyword_topic)와 csv 양쪽에 두고, 한쪽이 바뀌면 다른 쪽에 반영합니다. 둘 다 원본입니다.

csv (이 폴더)
- keyword_categories.csv: 분류 틀(대분류·중분류). category_rules.TAX의 23개 + 관리자가 만든 분류
- topic_categories.csv·synonyms.csv: build/가 만드는 팀 검수 사전. 코드 결과와 같아야 하므로(build/verify.py) 고치지 않습니다.
- topic_additions.csv: 그 밖에 더한 주제어. 상태 = 신규(단신속보 등에서 새로 나온 말, 분야는 규칙으로 판정) /
  분류·미분류·동의어(관리자 결정). 기사 처리(keyword_clean.py)가 팀 검수 사전 위에 덮어 씁니다.
DB keyword_topic의 상태는 csv로 기사 처리와 같은 판정을 해서 채웁니다:
  CLASSIFIED 분야 있음 / UNCLASSIFIED 분야 없음으로 확정 / PENDING 검수 대기(관리자 분류 대상) / SYNONYM 동의어

sync 순서
1. DB → csv: updated_at이 synced_at보다 나중인 행(관리자 기능이나 SQL로 바꾼 행)을 csv에 씁니다.
   분류 틀 → keyword_categories.csv, 주제어 → topic_additions.csv (CLASSIFIED·UNCLASSIFIED·SYNONYM은 관리자 결정, PENDING은 신규)
2. csv → DB: csv로 판정한 결과와 다른 행은 고치고, 없는 행은 넣고, csv에 없는 행은 지웁니다. 맞춘 행은 synced_at = updated_at.
같은 말을 양쪽에서 고쳤으면 DB 쪽이 이깁니다 (1이 먼저 csv에 씀).
DB에서 행을 지우면 다음 sync에 csv 값으로 다시 생깁니다. 지우려면 csv(topic_additions.csv)에서 지웁니다. 분류 틀은 지우지 않습니다.

실행 (저장소 루트에서): python -m backend.app.services.news_keywords.keyword_dict sync
  pending [--limit 50]              검수 대기 주제어 (기사 수 많은 순)
  classify 단어 "대분류>중분류"      분야 지정 ("미분류"면 분야 없음으로 확정)
  category 대분류 중분류             새 분류 만들기
  synonym 표기 대표어                동의어로 합치기 (기사 태그도 대표어로 바꿈)
스케줄러(backend/app/scheduler/main.py)가 10분마다 sync를 실행합니다.
"""
import argparse
import csv
import logging
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, or_, select, update

from backend.app.db.session import make_engine, session_scope
from backend.app.models.article import ArticleTag
from backend.app.models.keyword import KeywordCategory, KeywordTopic
from backend.app.services.news_keywords import keyword_clean as kc

CATEGORY_PATH = kc.BASE / "keyword_categories.csv"
CATEGORY_COLUMNS = ["대분류", "중분류"]
ADDITIONS_PATH = kc.ADDITIONS_PATH
NONE = "미분류"
# ponytail: 한 프로세스 안에서만 막음. 스케줄러와 수동 실행이 같은 순간 csv를 쓰면 한쪽 변경이 다음 sync로 밀림
_lock = threading.Lock()
log = logging.getLogger("keyword_dict")


def read_csv(path):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, columns, rows):
    """다 쓴 뒤에만 이름을 바꿔, 중간에 멈춰도 반쯤 쓴 파일이 남지 않게 합니다."""
    tmp = Path(path).with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def load_rules():
    """기사 처리에 쓰는 사전 (지역, 예외, 동의어, 분류). 관리자 결정까지 반영합니다."""
    return (kc.load_geo(), kc.load_exceptions(), kc.load_synonyms(kc.SYNONYMS_PATH, ADDITIONS_PATH),
            kc.load_categories(kc.CATEGORIES_PATH, additions=ADDITIONS_PATH))


def topics(keywords, rules):
    """키워드 목록 → 주제어 목록 (정제·지역 분리·연도 분리·동의어). 해외시장뉴스 키워드와 같은 처리입니다."""
    geo, keep, syn, cat = rules
    return kc.process_article(",".join(keywords), "", geo, keep, syn, cat)["kw_topic"]


def topic_view(rules=None):
    """csv로 판정한 주제어 상태: 주제어 → (대표어, (대분류, 중분류) 또는 None, 상태)."""
    _, _, syn, cat = rules or load_rules()
    words = set(cat.listed) | set(syn) | set(syn.values()) | set(kc.load_additions(ADDITIONS_PATH))
    view = {}
    for w in words:
        if w in syn:
            view[w] = (syn[w], None, "SYNONYM")
        elif c := cat.lookup(w):
            view[w] = (None, tuple(c.split(">", 1)), "CLASSIFIED")
        else:
            view[w] = (None, None, "UNCLASSIFIED" if cat.settled(w) else "PENDING")
    return view


def _dirty(model):
    return or_(model.synced_at.is_(None), model.updated_at > model.synced_at)


def pull_db_changes(s):
    """1단계: csv에 아직 반영하지 않은 DB 변경을 csv에 씁니다. (분류 틀 수, 주제어 수)"""
    cats = s.scalars(select(KeywordCategory).where(_dirty(KeywordCategory))).all()
    if cats:
        have = [(r["대분류"], r["중분류"]) for r in read_csv(CATEGORY_PATH)]
        new = [(c.major_name, c.minor_name) for c in cats if (c.major_name, c.minor_name) not in have]
        if new:
            write_csv(CATEGORY_PATH, CATEGORY_COLUMNS,
                      [dict(zip(CATEGORY_COLUMNS, pair)) for pair in have + new])
    rows = s.execute(select(KeywordTopic, KeywordCategory).outerjoin(KeywordCategory)
                     .where(_dirty(KeywordTopic))).all()
    if rows:
        adds = {kc.normalize(r["주제어"]): r for r in read_csv(ADDITIONS_PATH) if r.get("주제어")}
        for t, c in rows:
            state = {"PENDING": "신규", "CLASSIFIED": "분류", "UNCLASSIFIED": "미분류", "SYNONYM": "동의어"}[t.status]
            adds[t.keyword_text] = {"주제어": t.keyword_text, "상태": state, "대표어": t.canonical_text or "",
                                    "대분류": c.major_name if c else "", "중분류": c.minor_name if c else "",
                                    "수정일": f"{t.updated_at:%Y-%m-%d}"}
        write_csv(ADDITIONS_PATH, kc.ADDITION_COLUMNS, sorted(adds.values(), key=lambda r: r["주제어"]))
    return len(cats), len(rows)


def push_csv(s, now):
    """2단계: csv로 판정한 결과에 DB를 맞춥니다. {"inserted", "updated", "deleted"}"""
    view = topic_view()
    listed = [(r["대분류"], r["중분류"]) for r in read_csv(CATEGORY_PATH)]
    needed = list(dict.fromkeys([*listed, *((b, m) for b, m, _ in kc.cr.TAX),
                                 *(c for _, c, _ in view.values() if c)]))
    if needed != listed:  # 팀 사전에 쓰였는데 분류 틀 csv에 없는 분류는 틀에 더함
        write_csv(CATEGORY_PATH, CATEGORY_COLUMNS, [dict(zip(CATEGORY_COLUMNS, pair)) for pair in needed])
    ids = {(c.major_name, c.minor_name): c for c in s.scalars(select(KeywordCategory))}
    for pair in needed:
        if pair not in ids:
            ids[pair] = KeywordCategory(major_name=pair[0], minor_name=pair[1])
            s.add(ids[pair])
    for c in ids.values():
        if c.synced_at is None or c.synced_at != c.updated_at:
            c.updated_at = c.synced_at = now
    s.flush()
    db = {t.keyword_text: t for t in s.scalars(select(KeywordTopic))}
    counts = {"inserted": 0, "updated": 0, "deleted": 0}
    for word, (canonical, pair, status) in view.items():
        want = {"canonical_text": canonical, "category_id": ids[pair].category_id if pair else None, "status": status}
        t = db.pop(word, None)
        if t is None:
            s.add(KeywordTopic(keyword_text=word, **want, updated_at=now, synced_at=now))
            counts["inserted"] += 1
        elif any(getattr(t, k) != v for k, v in want.items()) or t.synced_at != t.updated_at:
            counts["updated"] += any(getattr(t, k) != v for k, v in want.items())
            for k, v in want.items():
                setattr(t, k, v)
            t.updated_at = t.synced_at = now
    for t in db.values():
        s.delete(t)
        counts["deleted"] += 1
    return counts


def sync(engine, now=None):
    """DB 변경을 csv에 쓰고(1), csv로 DB를 맞춥니다(2). 결과 건수."""
    with _lock, session_scope(engine) as s:
        cats, topics_pulled = pull_db_changes(s)
        counts = push_csv(s, now or datetime.now())
    counts |= {"pulled_categories": cats, "pulled_topics": topics_pulled}
    log.info(f"키워드 사전 sync: {counts}")
    return counts


def scheduled_sync():
    """스케줄러용. 실패해도 다음 주기에 다시 하므로 로그만 남깁니다."""
    engine = make_engine()
    try:
        sync(engine)
    except Exception:
        log.exception("키워드 사전 sync 실패 (10_keyword_dictionary.sql로 테이블을 만들었는지 확인)")
    finally:
        engine.dispose()


def register_topics(engine, words):
    """DB에 없는 주제어를 검수 대기로 넣고 sync합니다 (csv에는 신규로 들어가고, 규칙으로 분야가 정해지면 CLASSIFIED). 새로 넣은 수."""
    with session_scope(engine) as s:
        have = set(s.scalars(select(KeywordTopic.keyword_text)))
        new = sorted(set(words) - have)
        s.add_all(KeywordTopic(keyword_text=w, status="PENDING", updated_at=datetime.now()) for w in new)
    sync(engine)
    return len(new)


def _category(s, major, minor):
    c = s.scalar(select(KeywordCategory).where(KeywordCategory.major_name == major,
                                               KeywordCategory.minor_name == minor))
    if c is None:
        raise ValueError(f"없는 분류입니다: {major}>{minor} (먼저 category로 만드세요)")
    return c


def _topic(s, word):
    return s.get(KeywordTopic, word) or KeywordTopic(keyword_text=word)


def classify_topic(engine, word, category):
    """관리자가 주제어의 분야를 정합니다. category = "대분류>중분류", "미분류"면 분야 없음으로 확정."""
    word = kc.normalize(word)
    with session_scope(engine) as s:
        t = _topic(s, word)
        if category == NONE:
            t.category_id, t.status = None, "UNCLASSIFIED"
        else:
            major, _, minor = category.partition(">")
            t.category_id, t.status = _category(s, major.strip(), minor.strip()).category_id, "CLASSIFIED"
        t.canonical_text, t.updated_at = None, datetime.now()
        s.add(t)
    return sync(engine)


def create_category(engine, major, minor):
    """관리자가 새 분류(대분류>중분류)를 만듭니다. 이미 있으면 그대로 둡니다."""
    major, minor = major.strip(), minor.strip()
    if not major or not minor or ">" in major + minor or NONE in (major, minor):
        raise ValueError("대분류·중분류는 비어 있지 않아야 하고 '>'나 '미분류'를 쓸 수 없습니다")
    with session_scope(engine) as s:
        if s.scalar(select(KeywordCategory).where(KeywordCategory.major_name == major,
                                                  KeywordCategory.minor_name == minor)) is None:
            s.add(KeywordCategory(major_name=major, minor_name=minor, updated_at=datetime.now()))
    return sync(engine)


def merge_synonym(engine, alias, canonical):
    """관리자가 alias를 canonical의 동의어로 합칩니다. 이미 붙은 기사 태그(article_tag)도 대표어로 바꿉니다."""
    alias, canonical = kc.normalize(alias), kc.normalize(canonical)
    with session_scope(engine) as s:
        rep = s.get(KeywordTopic, canonical)
        if rep is None:
            raise ValueError(f"대표어 '{canonical}'가 사전에 없습니다")
        canonical = rep.canonical_text or canonical  # 대표어가 이미 다른 말의 동의어면 그 대표어로
        if canonical == alias:
            raise ValueError(f"'{alias}'를 자기 자신의 동의어로 만들 수 없습니다")
        t = _topic(s, alias)
        t.canonical_text, t.category_id, t.status, t.updated_at = canonical, None, "SYNONYM", datetime.now()
        s.add(t)
        # 이 말을 대표어로 쓰던 동의어도 새 대표어로 (A→alias였으면 A→canonical)
        s.execute(update(KeywordTopic).where(KeywordTopic.canonical_text == alias)
                  .values(canonical_text=canonical, updated_at=datetime.now()))
        # 두 말이 같이 붙은 기사는 alias 태그만 지움 (MySQL은 같은 테이블을 하위 질의로 지울 수 없어 번호를 먼저 읽음)
        both = list(s.scalars(select(ArticleTag.article_id).where(ArticleTag.keyword_text == canonical)))
        if both:
            s.execute(delete(ArticleTag).where(ArticleTag.keyword_text == alias, ArticleTag.article_id.in_(both)))
        retagged = s.execute(update(ArticleTag).where(ArticleTag.keyword_text == alias)
                             .values(keyword_text=canonical)).rowcount
    log.info(f"동의어: {alias} → {canonical} | 기사 태그 {retagged}건 변경")
    return sync(engine)


def pending_topics(engine, limit=50):
    """검수 대기 주제어와 그 주제어가 붙은 기사 수 [(주제어, 기사 수)], 기사 수 많은 순."""
    n = func.count(func.distinct(ArticleTag.article_id))
    with session_scope(engine) as s:
        return s.execute(select(KeywordTopic.keyword_text, n)
                         .outerjoin(ArticleTag, ArticleTag.keyword_text == KeywordTopic.keyword_text)
                         .where(KeywordTopic.status == "PENDING").group_by(KeywordTopic.keyword_text)
                         .order_by(n.desc(), KeywordTopic.keyword_text).limit(limit)).all()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="키워드 사전 DB·csv 맞추기와 관리자 기능")
    ap.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync")
    p = sub.add_parser("pending")
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("classify")
    p.add_argument("word")
    p.add_argument("category", help='"대분류>중분류" 또는 "미분류"')
    p = sub.add_parser("category")
    p.add_argument("major")
    p.add_argument("minor")
    p = sub.add_parser("synonym")
    p.add_argument("alias")
    p.add_argument("canonical")
    args = ap.parse_args()
    engine = make_engine(args.db_url)
    try:
        if args.cmd == "sync":
            print(sync(engine))
        elif args.cmd == "pending":
            for word, n in pending_topics(engine, args.limit):
                print(f"{n:>5}  {word}")
        elif args.cmd == "classify":
            print(classify_topic(engine, args.word, args.category))
        elif args.cmd == "category":
            print(create_category(engine, args.major, args.minor))
        else:
            print(merge_synonym(engine, args.alias, args.canonical))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
