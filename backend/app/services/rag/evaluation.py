"""기존 검색 기준선 기록과 오프라인 기사 단위 평가. 검수된 최종 50문항은 문항별 평균으로 판정합니다."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, StrictBool, StrictInt, model_validator

from backend.app.schemas.rag import ALL_SOURCES, ArticleKey, Contract, RagRequest, Text, _unique
from backend.app.services.news_collections import ARTICLE_COLLECTION
from backend.app.services.rag.store import COLLECTIONS


class EvaluationCase(Contract):
    case_id: Text
    request: RagRequest
    # null=미판정, []=정답 기사 없음. 둘을 같게 처리하지 않습니다.
    relevant_articles: tuple[ArticleKey, ...] | None

    @model_validator(mode="after")
    def check_labels(self):
        if self.relevant_articles is not None:
            _unique(tuple(a.key for a in self.relevant_articles), "정답 기사")
        return self


class EvaluationDataset(Contract):
    schema_version: Literal["rag-evaluation-v1"] = "rag-evaluation-v1"
    dataset_id: Text
    split: Literal["dev", "holdout"]
    synthetic: StrictBool
    reviewed: StrictBool = False
    review_reference: Text | None = None
    cases: Annotated[tuple[EvaluationCase, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def check_ids(self):
        if self.reviewed and not self.review_reference:
            raise ValueError("검수 완료 자료에는 검수 기록 참조가 필요합니다")
        _unique(tuple(c.case_id for c in self.cases), "평가 문항 ID")
        _unique(tuple(c.request.request_id for c in self.cases), "요청 ID")
        return self


class RunConfiguration(Contract):
    configuration_id: Text
    retriever_revision: Text
    corpus_revision: Text
    finalized: StrictBool = False
    embedding_model: Text
    chunk_chars: Annotated[StrictInt, Field(gt=0)]
    overlap_chars: Annotated[StrictInt, Field(ge=0)]
    collections: tuple[Text, ...]
    baseline_kind: Literal['reconstructed', 'existing_index'] | None = None


class CaseResult(Contract):
    case_id: Text
    request_id: Text
    outcome: Literal["recorded", "error", "unsupported"]
    retrieved: tuple[ArticleKey, ...] = ()  # 고유 기사, 검색 순서 유지
    raw_chunk_count: Annotated[StrictInt, Field(ge=0)] | None = None
    latency_ms: Annotated[float, Field(ge=0)]
    limitations: tuple[Text, ...] = ()
    error_code: Text | None = None

    @model_validator(mode="after")
    def check_result(self):
        _unique(tuple(a.key for a in self.retrieved), "검색 기사")
        if self.outcome == "recorded":
            if self.error_code is not None:
                raise ValueError("정상 기록에 오류 코드를 붙일 수 없습니다")
        elif self.error_code is None or self.retrieved:
            raise ValueError("실패·미지원은 오류 코드와 빈 검색 결과로 기록합니다")
        if self.raw_chunk_count is not None and self.raw_chunk_count < len(self.retrieved):
            raise ValueError("청크 수는 고유 기사 수보다 작을 수 없습니다")
        return self


class RetrievalRun(Contract):
    run_id: Text
    dataset_fingerprint: Text
    recorded_at: datetime
    configuration: RunConfiguration
    results: tuple[CaseResult, ...]

    @model_validator(mode="after")
    def check_ids(self):
        _unique(tuple(r.case_id for r in self.results), "실행 문항 ID")
        if self.recorded_at.tzinfo is None:
            raise ValueError("실행 시각에는 시간대가 필요합니다")
        return self


def fingerprint(dataset: EvaluationDataset) -> str:
    payload = json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check_holdout(dataset, configuration):
    if dataset.split == "holdout" and not configuration.finalized:
        raise ValueError("Holdout은 최종 설정 확정 후에만 실행·평가할 수 있습니다")


def record_legacy_baseline(dataset: EvaluationDataset, search, configuration: RunConfiguration) -> RetrievalRun:
    """search는 기존 news_embed.search와 같은 호출 형태. 검색 방식을 개선하지 않습니다.

    기존 n=5 청크 결과에서 기사 ID만 중복 제거합니다. 부족한 기사를 재검색하지
    않으므로 현재 청크 중복에 의한 누락도 기준선에 남습니다. 문서 본문은 기록하지 않습니다.
    """
    _check_holdout(dataset, configuration)
    if configuration.collections != (ARTICLE_COLLECTION,):
        raise ValueError(f"기존 검색 기준선의 컬렉션은 {ARTICLE_COLLECTION} 하나입니다")
    results = []
    for case in dataset.cases:
        request = case.request
        geo = request.conditions.geography
        limitations = ["legacy_chunk_top5", "single_collection_only", "dictionary_query_not_connected"]
        if request.conditions.products:
            limitations.append("hs_metadata_filter_not_supported")
        if request.conditions.blocked_topics:
            limitations.append("user_block_not_supported")
        unsupported = None
        if request.use_case != "chat":
            unsupported = "legacy_baseline_is_chat_only"
        elif len(geo.codes) > 1:
            unsupported = "legacy_multiple_countries_not_supported"
        start = perf_counter()
        common = dict(case_id=case.case_id, request_id=request.request_id, limitations=tuple(limitations))
        if unsupported:
            results.append(CaseResult(**common, outcome="unsupported", error_code=unsupported, latency_ms=0))
            continue
        try:
            raw = list(search(
                request.query, n=5,
                country=geo.codes[0] if geo.mode == "selected" else None,
                date_from=request.conditions.period.start.isoformat(),
                date_to=request.conditions.period.end.isoformat(),
            ))
            ranked, seen = [], set()
            for _, _, _, metadata in raw:
                article = ArticleKey(source_code=metadata["source_code"], external_id=metadata["external_id"])
                if article.key not in seen:
                    ranked.append(article)
                    seen.add(article.key)
            results.append(CaseResult(**common, outcome="recorded", retrieved=tuple(ranked),
                                      raw_chunk_count=len(raw), latency_ms=(perf_counter() - start) * 1000))
        except Exception as exc:
            # 예외 메시지에는 원문·접속 정보가 섞일 수 있어 유형만 보존합니다.
            results.append(CaseResult(**common, outcome="error", error_code=type(exc).__name__,
                                      latency_ms=(perf_counter() - start) * 1000))
    return RetrievalRun(run_id=uuid4().hex, dataset_fingerprint=fingerprint(dataset),
                        recorded_at=datetime.now(timezone.utc), configuration=configuration,
                        results=tuple(results))


def evaluate(dataset: EvaluationDataset, run: RetrievalRun) -> dict:
    """최종 집계는 문항별 평균입니다. 전체 정답 합산은 보조 지표입니다.

    정답 없음은 Recall 분모에서 별도로 보고합니다. 오류·미실행 양성 문항의
    hit는 0으로 남겨 실패 문항을 빼고 성능이 높아지는 일을 막습니다.
    """
    _check_holdout(dataset, run.configuration)
    if run.dataset_fingerprint != fingerprint(dataset):
        raise ValueError("실행 기록과 평가 문항·정답의 지문이 다릅니다")
    by_case = {r.case_id: r for r in run.results}
    if set(by_case) - {c.case_id for c in dataset.cases}:
        raise ValueError("평가셋에 없는 실행 문항입니다")
    rows, recalls = [], []
    hits_total = relevant_total = no_answer = correct_empty = unlabeled = failures = not_run = 0
    for case in dataset.cases:
        result = by_case.get(case.case_id)
        if result and result.request_id != case.request.request_id:
            raise ValueError("문항과 실행 기록의 요청 ID가 다릅니다")
        outcome = result.outcome if result else "not_run"
        not_run += result is None
        failures += outcome in ("error", "unsupported")
        ranked = list(result.retrieved[:5]) if result and outcome == "recorded" else []
        row = {"case_id": case.case_id, "outcome": outcome,
               "retrieved_top5": [a.model_dump(mode="json") for a in ranked], "recall_at_5": None,
               "limitations": list(result.limitations) if result else [],
               "error_code": result.error_code if result else None}
        if case.relevant_articles is None:
            unlabeled += 1
            row["label_state"] = "unlabeled"
        elif not case.relevant_articles:
            no_answer += 1
            correct = outcome == "recorded" and not ranked
            correct_empty += correct
            row.update(label_state="no_answer", correctly_empty=correct)
        else:
            gold = {a.key for a in case.relevant_articles}
            hits = len(gold & {a.key for a in ranked})
            hits_total += hits
            relevant_total += len(gold)
            recalls.append(hits / len(gold))
            row.update(label_state="positive", relevant_count=len(gold), hits=hits, recall_at_5=hits / len(gold))
        rows.append(row)
    macro = sum(recalls) / len(recalls) if recalls else None
    eligible = (not dataset.synthetic and dataset.reviewed and dataset.split == "holdout"
                and run.configuration.finalized and len(dataset.cases) == len(recalls) == 50
                and not not_run
                and not any(r.limitations for r in run.results)
                and set(run.configuration.collections) == set(COLLECTIONS))
    acceptance = ("passed" if macro >= 0.7 else "failed") if eligible else "not_assessed"
    return {
        "run_id": run.run_id, "dataset_id": dataset.dataset_id, "dataset_fingerprint": fingerprint(dataset),
        "synthetic": dataset.synthetic, "split": dataset.split,
        "case_count": len(dataset.cases), "labeled_positive_cases": len(recalls),
        "unlabeled_cases": unlabeled, "no_answer_cases": no_answer,
        "correctly_empty_cases": correct_empty, "failed_or_unsupported_cases": failures, "not_run_cases": not_run,
        "macro_recall_at_5": sum(recalls) / len(recalls) if recalls else None,
        "micro_recall_at_5": hits_total / relevant_total if relevant_total else None,
        "required_question_count": 50, "required_recall_at_5": 0.7,
        "acceptance_status": acceptance, "aggregation": "macro_recall_at_5",
        "gold_sources": sorted({a.source_code.value for c in dataset.cases for a in (c.relevant_articles or ())}),
        "acceptance_note": "검수된 별도 최종 50문항·확정 설정·공통 total_article 컬렉션 실행에만 합격 판정을 적용합니다.",
        "cases": rows,
    }


def write_new_json(path, value):
    """동일 경로의 기준선·실패 기록을 덮어쓰지 않습니다."""
    path = Path(path)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)


class ExistingCollectionsOnly:
    """기존 검색 함수의 생성 API를 조회로 연결해 없는 컬렉션을 새로 만들지 않습니다."""
    def __init__(self, client):
        self.client = client

    def get_collection(self, name, **kwargs):
        return self.client.get_collection(name, embedding_function=None)

    def get_or_create_collection(self, name, **kwargs):
        return self.get_collection(name, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="평가 입력 형식 확인. 검색·모델 호출 없음")
    validate.add_argument("--dataset", type=Path, required=True)
    score = commands.add_parser("score", help="기록된 실행 결과를 오프라인 평가")
    score.add_argument("--dataset", type=Path, required=True)
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    capture = commands.add_parser("capture-legacy", help="기존 검색 실행. 질의 임베딩 호출이 발생합니다")
    capture.add_argument("--dataset", type=Path, required=True)
    capture.add_argument("--chroma-path", type=Path, required=True)
    capture.add_argument("--snapshot", type=Path, help="재구성 기준선의 보존 코드 폴더. 지정하면 해시 검증 후 해당 검색 코드를 실행합니다")
    capture.add_argument("--configuration-id", required=True)
    capture.add_argument("--corpus-revision", required=True)
    capture.add_argument("--final-configuration", action="store_true", help="최종 설정 확정 후 Holdout을 사용할 때 지정")
    capture.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    dataset = EvaluationDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    if args.command == "validate":
        print(json.dumps({"dataset_id": dataset.dataset_id, "cases": len(dataset.cases),
                          "synthetic": dataset.synthetic, "unlabeled": sum(c.relevant_articles is None for c in dataset.cases),
                          "baseline_measured": False}, ensure_ascii=False))
        return 0
    if args.output.exists():
        raise FileExistsError("기존 기록을 보존합니다. 새 출력 경로를 지정하세요")
    acceptance_status = 'not_assessed'
    if args.command == "score":
        run = RetrievalRun.model_validate_json(args.run.read_text(encoding="utf-8"))
        report = evaluate(dataset, run)
        write_new_json(args.output, report)
        acceptance_status = report['acceptance_status']
    else:
        if not (args.chroma_path / "chroma.sqlite3").is_file():
            raise ValueError("기존 Chroma 저장 경로가 필요합니다. 새 색인은 생성하지 않습니다")
        if dataset.split == "holdout" and not args.final_configuration:
            raise ValueError("Holdout은 최종 설정 확정 후에만 실행할 수 있습니다")
        from backend.app.services.rag.store import persistent_client
        if dataset.split == 'holdout' and not dataset.synthetic and not dataset.reviewed:
            raise ValueError('holdout_review_required_before_execution')
        if args.snapshot:
            from backend.app.services.rag.baseline import load_preserved_search
            news_embed, code_hash = load_preserved_search(args.snapshot)
        else:
            from backend.app.scheduler.jobs import news_embed
            code_hash = hashlib.sha256(Path(news_embed.__file__).read_bytes()).hexdigest()
        owned_client = persistent_client(args.chroma_path)
        client = ExistingCollectionsOnly(owned_client)
        failure = None
        try:
            news_embed.get_collection(client, news_embed.ARTICLE_COLLECTION)
            configuration = RunConfiguration(
                configuration_id=args.configuration_id,
                retriever_revision=code_hash,
                corpus_revision=args.corpus_revision, finalized=args.final_configuration,
                embedding_model=news_embed.MODEL, chunk_chars=news_embed.CHUNK, overlap_chars=news_embed.OVERLAP,
                collections=(news_embed.ARTICLE_COLLECTION,),
                baseline_kind='reconstructed' if (args.chroma_path.parent/'reconstruction.json').exists() else 'existing_index',
            )
            # SDK는 실제 검색할 때만 호출됩니다. 누적 토큰·색인 변경은 이 단계의 합격 근거가 아닙니다.
            run = record_legacy_baseline(dataset, lambda query, **kw: news_embed.search(query, client=client, **kw), configuration)
            write_new_json(args.output, run.model_dump(mode="json"))
        except BaseException as exc:
            failure = exc
            raise
        finally:
            try:
                owned_client.close()
            except Exception as exc:
                if failure is None:
                    raise
                failure.add_note('legacy client cleanup failed: ' + type(exc).__name__)
    print(json.dumps({"output": str(args.output), "acceptance_status": acceptance_status}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
