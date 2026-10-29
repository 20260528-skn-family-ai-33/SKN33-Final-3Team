# 공통 기사 RAG 사용 및 검증

> 이름 변경: `kotra_article`은 `total_article`로 변경되었습니다. 과거 시험의 원본 결과·로그는 보존하며, 이 문서의 컬렉션 표기는 현재 이름을 사용합니다.

확정 목표와 단계별 상태는 [구현 계획](IMPLEMENTATION_PLAN.md)에 기록한다. 현재 공통 색인·검색·근거 요약·호출 어댑터와 일별 처리 연결 코드를 제공한다. 실제 네 출처 색인 운영, 정답 검수, 검색 품질 및 서비스 통합은 별도로 검증해야 한다.

## 호출 계약과 사용 방법

`RagRequest → RagResponse` 계약을 유지한다. 국가 누락과 명시적 전체 국가를 구분하며, 확정 HS4/HS6·판본, 발행일 양끝 포함 기간, 정규화 차단 주제어를 받는다. 품목·국가 목록 내부는 OR, 서로 다른 조건은 AND다. 현지 이슈는 국가 또는 품목이 필요하다. 코드·판본의 사용자 확정과 기본 기간 결정은 호출자 책임이다.

| 사용처 | 목표 기사 수 |
|---|---|
| 챗봇 | 5건, 기사별 1~2문장 |
| 현지 이슈 | 기본 5건, 최대 7건 |
| 주간 리포트 | 기본 5건, 최대 7건. 직전 주 월~일 |
| 그 외 리포트 현지 이슈 | 호출자가 3·4·5 중 지정 |

모든 건수는 요청한 출처 범위 안의 고유 기사 기준이다. `source_codes="ALL"`은 준비된 허용 출처만 사용한다. 단신만 요청할 때는 `["KOTRA_SHORT_NEWS"]`, 일일 신문은 `["HANKYUNG","FNNEWS"]`를 전달한다. 명시한 출처가 미준비이면 실패로 구분하며 범위를 다른 출처로 대체하지 않는다. 빈 배열·미지원 코드·중복 코드는 거부한다. 적합한 기사가 부족하면 실제 건수만 반환한다. 주간 `limit` 생략은 5건이며, 실제 실행일 기준 직전 주 선택은 호출자가 담당한다.

```python
import time
from openai import OpenAI
from backend.app.schemas.rag import RagRequest
from backend.app.services.rag.factory import create_service
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded
from backend.app.services.rag.service import rag_node

request = RagRequest.model_validate(request_json)
# deadline은 전체 요청을 시작한 호출자가 산정한 time.monotonic() 기준 마감 시각이다.
context = ExecutionContext(deadline=deadline, trace_id=request.request_id)
with OpenAI(max_retries=0) as api:
    service = create_service(api)
    result = service.run(request, context)
    # State에서는 service.run 대신 다음 어댑터를 호출한다.
    # patch = rag_node({"rag_request": request}, service, context)
```

`rag_node`는 `rag_result`, `rag_trace`만 반환하고 입력 State를 변경하지 않는다. `RagDeadlineExceeded`는 호출자가 공통 종료 정책으로 처리한다. 모델 요청에 남은 시간과 재시도 0회를 전달하며 구조화 출력 검증 실패만 1회 재요청한다. 로컬 Chroma 호출의 전후에도 마감 시각을 확인한다. 동기 Chroma 작업 자체의 강제 취소와 전체 서비스 응답시간은 실제 통합에서 검증해야 한다.

| 상태 | 의미 |
|---|---|
| `ok` | 모든 출처가 정상 처리되고 목표 건수의 근거·요약 반환 |
| `no_results` | 모든 출처가 정상 처리되었으나 조건에 맞는 기사 없음 |
| `insufficient_results` | 정상 처리했지만 목표보다 적은 양수의 기사 반환 |
| `insufficient_evidence` | 본문이나 설명 근거 부족으로 요약 보류 |
| `partial` | 일부 실패가 있지만 반환할 정상 기사도 있음 |
| `failed` | 실패로 반환할 기사가 없음 |

`requested_source_codes`는 요청 범위, `applied_source_codes`는 적용 범위, `completed_sources`는 처리 완료 출처이며 반환 기사에 등장한 출처 목록과 다르다. 명시적으로 요청한 미색인 출처·검색 오류를 정상 0건으로 처리하지 않는다. ALL의 준비된 출처가 하나도 없으면 실패다. `contract_examples.json`에는 상태 예시와 주간 예시가 있다.

## 색인 구조와 출처별 처리

| 출처 | 컬렉션 | 현재 연결 |
|---|---|---|
| KOTRA 해외시장뉴스 | `total_article` | 기존 기사·태그와 원본 보조 정보 → 공통 어댑터 → 세대 색인 |
| KOTRA 단신속보 | `total_article` | 기존 분석 결과·확정 태그 → 공통 어댑터 → 본문 색인 |
| 한국경제·파이낸셜뉴스 | `total_article` | 기존 일별 태그 생성·전달 재사용 → 공통 어댑터 → 본문 색인 |

`documents.py`는 기사 식별자, URL, 국가, 검증된 HS/판본, 최종 주제어·구체어, 분야, 본문과 메타데이터 미확보 상태를 정의한다. 출처 범위 밖 입력과 중복 기사 식별자는 거부한다.

`ingest.py`는 기존 태그의 코드·판본을 사전과 대조한다. KOTRA 원본이 있으면 저장 본문과 일치하는 경우에만 원래 키워드와 본문 확보 여부를 보완한다. 단신은 분석 결과를 전달하고, 최종 주제어에 연결된 구체어 중 본문에 실제 등장한 사전 표현을 함께 활용한다. 신문과 단신은 SQL에서 확정한 현재 기사 태그의 대표어를 그대로 유지한다. 파일 사전으로 다시 대표어를 바꾸지 않는다. 시장뉴스의 기존 주제어 정제는 유지한다. 과거 키워드는 현재 주제어에 연결되는 구체어 복원에만 사용하므로 본문 변경 없이 태그만 바뀐 경우도 반영한다. 복원하지 못한 원래 구체어는 `metadata_missing`에 남긴다. 기존 KOTRA 적재 함수는 본문이 없을 때 요약·제목을 대신 저장한다. 따라서 일치하는 원본으로 본문 여부를 확인하지 못하면 `body_origin` 미확보로 기록하고, 입력은 보존하되 본문 임베딩·요약에는 사용하지 않는다. `rag_index.refresh(..., raw_market=경로)`로 해당 원본을 전달해 재처리한다.

신문사 국가의 의미는 **언급 국가**다. 응답의 `country_role=mentioned`로도 표시한다. 교역 상대국으로 확정하지 않는다. 신문사는 검증된 HS 태그가 있으면 태그로 품목을 적용하고, 태그가 없으면 확정 품목의 사전 이름과 본문 관련성을 판단한다. 단신은 챗봇·현지 이슈·리포트 모두에서 HS 태그 유무만으로 제외하지 않고 본문 품목 관련성을 적용한다. 품목 관련성 판단은 원문 발췌를 요구하며 HS 코드를 만들어 붙이지 않는다.

구현 해석: FR10107의 포괄적인 HS 조건은 시장뉴스의 검증된 코드·판본 일치에 적용하고, FR10502의 단신 예외는 단신의 본문 관련성 판단으로 처리한다. 단신 예외를 현지 이슈에만 제한하면 챗봇에서 동일 기사가 빠지므로 두 경로에 같은 예외를 적용한다. 무관한 단신은 관련성 검증에서 제외하며, 차단 주제어는 관련성·요약 호출보다 먼저 적용한다. 이 설명은 구현 정책이며 원문 요구사항 파일을 수정한 것이 아니다.

기본 기사 색인은 `data/rag/index` 아래에 저장한다. `generations/<id>/chroma`마다 단일 본문 컬렉션 `total_article`을 유지하며, `active.json`은 검증을 통과한 세대만 가리킨다.

- 초기 모델은 `text-embedding-3-large`, 3072차원이다. 기존 청킹 함수의 1000자·150자 겹침을 사용한다.
- 공통 `news_embed.format_document`의 `[게시 날짜 | 국가 | 원천 분류] 제목\n본문 청크`를 Chroma 문서와 임베딩 입력에 동일하게 사용한다. 청크는 기존 `chunk_text`(1000자·150자 겹침) 결과를 그대로 사용한다. 본문 없는 기사는 별도 메타데이터 레코드로만 보관하고 유사도 검색 및 모델 요약 입력에서 제외한다.
- 키워드·URL처럼 헤더 밖의 메타데이터만 바뀌면 본문 벡터를 재사용한다. 제목·게시 날짜·국가·원천 분류는 헤더를 구성하므로 변경 시 입력도 달라져 재임베딩한다. 주제어 기반 `categories`와 원천 `category`는 별도 필드다.
- 세대 상태와 컬렉션에 `document_format=news-header-v1`, `chunk_chars=1000`, `overlap_chars=150`을 기록한다. 이전 제목+본문 형식은 `article_document_migration_required`로 거부하며 자동 이관·재임베딩하지 않는다.
- 출처별 전체 현재 입력을 교체하며 다른 출처는 보존한다. 동일 임베딩 입력의 벡터는 재사용한다.
- 작성·검증이 끝난 뒤 활성 참조를 교체한다. 독자는 요청 시작 시 색인과 사전을 같은 세대로 고정한다.
- 실패 세대와 이전 정상 세대를 보존한다. 배치별 처리 건수·단계와 저장된 벡터 내용을 검증하며, 중단된 작성 상태를 기록하고 재실행 시 새 세대로 복구한다. 이전 세대를 자동 삭제하지 않는다.
- 공통 사전이 바뀌면 일부 출처만 새로운 사전을 사용하는 혼합 게시를 거부한다. 준비된 모든 출처를 함께 갱신한다.
- 실제 본문·색인·캐시·평가 자료는 기존 `data/` 제외 규칙 아래에 둔다.

공통 키워드 사전 색인과 `short_news_keywords_<revision>` 분류용 색인은 기사 본문 색인과 분리한다. 본문 출처 분리가 공통 키워드 동기화 범위를 축소하지 않는다.

## 검색과 요약

색인 게시와 자원 정리의 결과를 구분합니다. `rag_index.run`에서 활성 세대 게시 후 임베딩 연결·DB 연결 종료만 실패하면 `index.status=ready`와 세대 ID를 보존하고 실행은 `partial`, 상세는 `cleanup_errors`로 기록합니다. 게시 자체의 오류가 있으면 원래 `error_code`를 유지합니다. 실행 보고서 저장 실패도 `report_error`로 반환합니다. 기본 `news_embed.run`은 이미 게시된 경우 공통 키워드 갱신을 계속 시도하지만 종료 코드는 1을 유지하므로 전체 성공으로 오인하지 않습니다.

평가·기준선 CLI는 직접 생성한 클라이언트와 임베딩 연결을 종료하며, 주입받은 연결은 호출자 소유로 남깁니다. 한국어 사전·캐시·평가 JSON/JSONL은 UTF-8로 읽습니다. 정식 평가의 50문항·검수된 비합성 holdout·확정 설정·Recall@5 0.7 기준은 유지하며 현재 단일 `total_article` 계약에 적용합니다. 합성 시험 통과는 실제 검색 품질 합격을 뜻하지 않습니다.

질문 임베딩을 한 번 생성해 단일 본문 컬렉션을 검색한다. 요청 `source_code` 필터를 국가·기간 조건과 함께 top-k 이전 조회에 적용하므로 다른 출처의 높은 점수 기사가 요청 출처의 슬롯을 차지하지 않는다. 같은 컬렉션을 출처 수만큼 무필터 조회하지 않는다. 차단·관련성 판정 후 부족하거나 동점이 있으면 동일한 출처 조건 안에서 후보 창만 확장한다. 주제어·동의어·상위어·구체어를 검색 문맥에 반영한다. 분야는 관련도가 같은 후보의 순위 보조에만 사용한다.

기사별 가장 가까운 청크 점수로 통합 순위를 정한다. `(source_code, external_id)`가 같을 때만 기사 중복을 제거한다. 분야가 다르거나 미분류라는 이유, 또는 서로 주제가 비슷하다는 이유로 기사를 제외하지 않는다. 차단·중복 제거 뒤 부족하면 같은 조건으로 추가 후보를 조회한다. 목표 수를 채웠더라도 컬렉션별 다음 후보가 현재 최하위 결과보다 좋거나 동점일 수 있으면 조회를 계속하며, 후보 소진과 전체 마감 시각 초과를 구분한다.

차단 주제어를 최종 주제어와 정확 일치로 비교한다. 차단은 품목 관련성 판단·요약 모델 호출보다 먼저 수행한다. 요약은 질문과 같은 기사의 원문 청크를 사용한다. 문장마다 근거 ID·청크 ID·발췌를 연결하고, 발췌가 해당 청크에 실제 포함되는지 검증한다.

캐시 읽기·쓰기 실패는 추적 정보에 남기고 정상 요약을 보존한다. 요약 캐시는 질문·조건·차단 목록·기사 및 본문 버전·모델·프롬프트 버전을 반영한다. 기사 내 명령은 자료로 취급하도록 프롬프트를 분리한다. 코드 검증은 근거 참조와 실제 발췌를 확인하며, 의미·사실·수치·인과관계의 정확성은 별도 원문 검수가 필요하다.

## 일별 처리와 명령

신문사 예약 작업은 `trade_news_daily.run_with_rag`를 호출한다. 사전 스냅샷 → 수집 → SQL 적재 → 공통 벡터 단계를 하나의 실행 잠금 안에서 제한 시간 있는 자식으로 수행한다. 색인 실패는 `vector` 결과와 전체 `partial` 상태로 기록하며 SQL 성공을 유지한다. `run`의 기본값은 기존 수집·SQL 단계이고, 예약 래퍼는 `include_vector=True`를 명시한다.

단신 기본 실행은 분류용 색인과 분석을 유지하고 본문 색인을 공통 게시 경로로 연결한다. 기존 테스트에서 명시적으로 주입하는 구형 클라이언트 경로는 호환 검증용으로 유지한다.

KOTRA 수집·정제 결과에는 다음 연결 명령을 사용한다. 수집 범위나 실행 시각을 새로 정하지 않는다. 현재 프로젝트에 KOTRA 전체 수집·정제의 예약 실행기가 없으므로 예약 호출 측 연결은 남아 있다.

```sh
.venv/bin/python -m backend.app.scheduler.jobs.market_rag_pipeline --raw <수집결과.csv> --clean <정제결과.csv>
.venv/bin/python -m backend.app.scheduler.jobs.rag_index --dry-run
.venv/bin/python -m backend.app.scheduler.jobs.rag_index
```

기존 `news_embed`의 기본 본문 실행은 KOTRA 전용 공통 색인으로 연결한다. `--keywords-only`는 기존 공통 키워드 범위를 유지한다. 기본 기사 조회는 활성 세대를 읽으며, 컬렉션이 없을 때 빈 컬렉션을 만들지 않는다. `--rebuild`로 이전 정상 세대를 삭제하지 않는다.

색인과 검색은 `store.resolve_root`를 공유한다. 우선순위는 명시한 `root` → 프로세스의 `NEWS_VECTOR_ROOT` → 저장소 기준 `data/rag/index`다. 상대 경로도 실행 cwd가 아닌 저장소 기준으로 해석한다. `news_embed.search(root=...)`와 `run_sources(root=...)`에 같은 경로를 주거나 동일 환경 설정을 사용한다. active 세대가 없고 구형 `chroma_data/total_article`만 있으면 `legacy_direct_index_migration_required`로 중단하며 빈 새 색인 생성이나 구형 검색으로 대체하지 않는다. 시험은 임시 legacy fixture만 사용하며 실제 이관은 실행하지 않는다.

실제 실행은 모델 호출을 발생시킬 수 있다. 최초 구현 검증에서는 실제 전체 본문 재색인·질문 모델 호출을 실행하지 않았다. 이후 사용자 승인한 보존 단신 5건으로 별도 색인·실제 모델 호출 3건을 수행했다. 범위와 결과는 [후속 실행·사용자 검수 안내](HUMAN_HANDOFF.md)에 기록했다. 기사 연결 조회는 연결 오류로 완료하지 못했다.

## 기준선·평가와 검수 자료

기존 상태 보존 위치: `data/rag/baselines/20261024_013843`.

- 276개 파일의 복사 전후 SHA-256 일치와 색인 미사용 상태를 확인했다.
- `short_news`: 기사 5개, 단신 분류용 컬렉션: 키워드 24,744개. 두 건수를 혼동하지 않는다.
- 기존 `total_article`은 없었다. 당시 KOTRA 검색 실측이나 해당 컬렉션 백업을 완료했다고 표시하지 않는다.
- `baseline.py`는 보존한 코드 해시를 검증하고 고정 입력으로 별도 기준선 색인을 만드는 실행 경로다. 실제 재구성 실행은 미완료다.

```sh
.venv/bin/python -m backend.app.services.rag.baseline \
  --snapshot data/rag/baselines/20261024_013843 \
  --rows <고정한_기존검색입력.json> --output <새_기준선_폴더>
.venv/bin/python -m backend.app.services.rag.evaluation validate --dataset docs/rag/evaluation_example.json
```

기존 검색 기록은 `evaluation capture-legacy`로 생성한다. 재구성 기준선은 `--snapshot data/rag/baselines/20261024_013843`을 함께 지정해 보존한 검색 코드의 해시를 확인하고 실행한다. 실행 설정의 `baseline_kind`로 재구성과 기존 색인을 구분한다. 새 검색 기록은 `evaluate_service`로 실행한다. 재구성 기준선은 기존 청크 상위 5개에서 고유 기사만 중복 제거한다. 새 검색과 범위·미지원 조건 차이를 기록하고, 같은 Dev set에서 한 번에 한 조건만 변경한다.

```sh
.venv/bin/python -m backend.app.services.rag.evaluate_service \
  --dataset <검수된_평가셋.json> --configuration <실행설정.json> \
  --index-root data/rag/index --seconds-per-request <호출시간상한> --output <새_실행결과.json>
.venv/bin/python -m backend.app.services.rag.evaluation score \
  --dataset <평가셋.json> --run <실행결과.json> --output <새_평가결과.json>
```

실행 설정의 `corpus_revision`은 실제 활성 세대 ID와 일치해야 한다. 실행 기록과 평가 입력 지문이 다르면 채점하지 않는다. 기존 실행·실패 기록은 덮어쓰지 않는다.

정답 `null`은 미판정, 빈 배열은 검수된 정답 없음, 한 개 이상은 복수 정답을 포함하는 정답 기사 목록이다. 최종 점수는 문항별 Recall@5 평균이며 오류 문항은 적중 0으로 남긴다. 합성·미검수·미실행 자료는 합격 판정하지 않는다. 실제 Holdout 검색 실행도 검수 완료 전에는 거부한다. 사용처를 챗봇으로 제한하거나 정답 출처별 할당량을 추가하지 않으며, 정답의 출처 범위는 `gold_sources`로 별도 보고한다. 네 출처 연결 검증은 전체 완료 기준으로 계속 관리한다. 검수 기록이 있는 실제 별도 최종 50문항, 확정 설정, 단일 본문 컬렉션 실행에만 0.7 기준을 적용한다. 이는 검색 지표 판정이며 서비스 전체 완료 판정과 다르다.

실제 기사 기반 미검수 초안은 `data/rag/evaluation/review_draft_20261024_v2`에 있다. Dev 10문항과 최종용 50문항의 정답은 모두 `null`이다. `review.html`에서 질문과 후보 기사 전체 본문을 확인할 수 있다. 현재 KOTRA 두 출처만 확보되어 신문사 자료와 질문 보완이 필요하다. 제목을 참고한 질문 초안이므로 자연스러움·관련성·추가 정답을 검수한 뒤 확정한다. Holdout은 최종 설정 확정 후 최종 검증에만 사용한다.

## 검증 명령

```sh
PYTHONDONTWRITEBYTECODE=1 ANONYMIZED_TELEMETRY=False .venv/bin/python -m unittest \
  backend.tests.test_rag_contract backend.tests.test_rag_evaluation \
  backend.tests.test_rag_pipeline backend.tests.test_rag_ingest backend.tests.test_rag_acceptance \
  backend.tests.test_rag_execution backend.tests.test_rag_review \
  backend.tests.test_rag_review_workflow backend.tests.test_rag_state_matrix \
  backend.tests.test_news_embed backend.tests.test_shortnews_analysis \
  backend.tests.test_shortnews_pipeline backend.tests.test_shortnews_keywords
PYTHONDONTWRITEBYTECODE=1 ANONYMIZED_TELEMETRY=False .venv/bin/python -m pytest -q \
  backend/tests/trade_news/test_daily_scheduler.py backend/tests/trade_news/test_project_tags.py
```

테스트는 가짜 모델·합성 기사·임시 색인을 사용한다. 실제 검색 Recall, 의미적 요약 정확성, 전체 서비스 성능을 증명하는 결과가 아니다.

2026-10-24 기능 검증: 공통 계약·색인·검색·근거·복구·평가 도구 및 기존 기사·단신 테스트 128개, 신문사 예약 작업·태그 테스트 43개가 통과했다(총 171개). 재구성 도구 테스트는 합성 입력으로 실행 경로만 확인했으며 실제 기준선을 측정한 것이 아니다.

재검토에서 수정한 문제와 남은 검증은 [코드 검토 기록](REVIEW_20261024.md)에 정리했다.

후속 실행에서는 실제 단신 5건의 부분 검증과 사용자 검수 도구를 추가했고, 관련 회귀 테스트 134개가 통과했다. 실제 원문 의미 검수와 최종 검색 품질 판정은 별도로 남아 있다.


### 2026-10-25 공통 배치 인터페이스

`news_embed.run_sources(sources, *, db_url=None, root=None, embed=None, dry_run=False, progress=None)`를 사용한다. 단신은 `['KOTRA_SHORT_NEWS']`, 일일 신문은 `['HANKYUNG', 'FNNEWS']`를 명시한다. 빈 목록·미지원 SOURCE는 쓰기 전에 거부한다. API의 `BREAKING` 별칭은 `source_adapter.normalize_article_type`에서 `SHORT_NEWS`로 정규화하며 저장 SOURCE는 바꾸지 않는다.

- 반환 상태: `complete`, `planned`, `partial`, `failed`. 실행 중 체크포인트는 `running`이다. `body`와 `keywords`에 각 상태·건수·오류를 기록한다. 본문 성공/키워드 실패는 `partial`이다.
- 본문은 지정 출처의 현재 DB 기사만 갱신한다. 다른 출처의 기사와 메타데이터는 이전 세대에서 보존한다. 숨김·삭제 기사는 지정 범위에서 다음 게시 세대에 반영한다. 과거 보고서나 이전 세대를 삭제하지 않는다.
- 본문의 `selected_articles`는 이번 지정 범위, `total_articles`는 다른 출처 보존분을 포함한 세대 전체이다. 0건도 정상 조회 결과이며 제외한 출처를 0건으로 보고하지 않는다.
- `keywords.scope=all_database_sources`는 본문 scope와 다르다. 전체 DB 조회 성공 뒤에만 기존 `news_keyword`를 동기화한다. 조회 오류는 실패이며 빈 counts로 바꾸지 않는다. 빈 전체 결과도 기존 후보를 삭제하지 않는 보수적 정책을 유지한다. 단신 분류용 키워드 컬렉션은 건드리지 않는다.
- 본문과 공통 키워드는 같은 `NEWS_VECTOR_ROOT`(기본 `data/rag/index`)를 사용한다. 본문은 활성 세대, 키워드는 `root/keywords/news_keyword`에 저장한다. 명시 `root`도 동일한 규칙이다. 시험은 반드시 임시 root와 시험 DB/fake embed를 함께 주입한다.
- 같은 root는 `sync.lock`, 세대는 `publish.lock`, 공통 키워드는 저장 경로의 `.lock`으로 보호한다. 기존 `--keywords-only`도 집계 조회부터 같은 키워드 잠금을 사용한다. 잠금 순서는 sync → publish → keyword이며 역순 중첩은 없다.
- `dry_run`은 DB에서 계획만 조회한다. 디렉터리·컬렉션·active 포인터를 만들거나 갱신하지 않고 Chroma 클라이언트·유료 모델을 만들지 않는다.
- `usage.body/keywords`는 제공자가 보고한 임베딩 토큰과 기존 단가에 따른 예상 비용이다. 제공자가 usage를 제공하지 않으면 `reported=false`로 구분한다. 공급자가 응답하지 못한 과금 내역은 추정하지 않는다.
- 기존 3개 컬렉션 세대는 `single_collection_migration_required`로 중단한다. 실제 이전·재임베딩·삭제는 이번 작업에서 실행하지 않았다. 기존 운영 root를 바로 전환할 수 있다고 간주하지 않는다.

### 단신 선택 적용과 공통 자원 계약

- 단신은 DB에서 확정한 `keyword_text` 대표어를 새 벡터의 `kw_topic`에 그대로 유지한다. 단신의 기존 SQL HS 태그는 삭제하지 않으며, 새 벡터 `products`에는 반영하지 않는다. 시장뉴스·한국경제·파이낸셜뉴스의 HS·대표어 처리 규칙은 유지한다.
- `GenerationStore.client_factory`는 호출마다 새 소유 클라이언트를 반환한다. 게시 함수가 연 이전·새 세대 핸들은 검증 후 active 게시 전에 닫는다. 종료 실패 시 새 세대를 게시하지 않는다. 외부에서 별도로 연 검색 snapshot은 별도 참조로 유지하며, snapshot 호출자가 사용 후 닫는다.
- `rag_index`가 보조정보를 읽으려고 연 임시 snapshot도 닫는다. 카탈로그·상태·포인터는 UTF-8로 읽고 쓴다. Windows 분기는 파일 flush/fsync와 replace를 유지하고 지원되지 않는 디렉터리 open/fsync만 생략한다. Windows 실환경 검증은 별도다.
- `sync_all_keywords`는 전체 DB 집계부터 동기화·소유 클라이언트 종료까지 같은 경로의 잠금을 유지한다. 기존 `run(client=...)`은 영속 클라이언트일 때 같은 키워드 잠금에 참여하지만 호출자가 준 클라이언트를 닫지 않는다. 초기화·종료 예외는 종료 코드 1이다. 메모리 클라이언트 시험은 디스크 잠금을 만들지 않는다.
- 크롤링 저장소의 기본 `run()`은 세대 색인 경로를 유지한다. 단신 저장소의 `_run_legacy` wrapper 전체를 복사하지 않는다. 양쪽 공통 `run_sources`·본문 포맷·세대 계약과 별개로 기존 standalone 진입점 동작이 다를 수 있다.
- 단신 분석 요약은 분석 캐시와 `shortnews_analysis_results.json`에 남는 별도 결과다. 공통 Chroma에 단신 요약 메타데이터를 추가한 것이 아니다.
- 공개 `news_embed.search`는 출처를 저장소 접근 전에 검증·중복 제거한다. `client`와 `root` 동시 입력은 `ambiguous_search_storage`, active 세대에 없는 요청 출처는 `requested_source_not_ready`로 거부한다. 명시 client 경로는 `get_collection`만 사용하며 빈 컬렉션을 만들지 않는다. 함수가 연 snapshot은 성공·실패 모두 종료하고 빌린 client는 닫지 않는다. 반환은 기존 `(id, 거리, 문서, 메타데이터)` 목록이다. 이 helper가 실시간 DB 노출 검사·본문 관련성 검수·기사 단위 중복 제거까지 수행한다는 의미는 아니다.


## 두 뉴스 파이프라인 통합 보완 (2026-10-26)

- 본문 root 해석 우선순위는 명시 root → NEWS_VECTOR_ROOT → data/rag/index이며 전역 키워드는 항상 같은 root/keywords입니다. 기본 news_embed·keywords-only CLI·공통 run_sources·검색 helper·서비스 factory가 같은 규칙을 사용합니다. 명시 주입한 구형 client는 별도 호환 경로로 호출자가 소유합니다.
- 구형 기본 색인 검사는 최종 root가 저장소 기본 root일 때 적용합니다. 같은 경로를 인자로 주거나 환경변수로 주어도 같습니다. 별도 root에서는 기본 구형 색인을 자동 읽거나 인수하지 않습니다.
- root/database.json에 비밀 없는 DB 해시를 연결합니다. 다른 DB는 source_database_mismatch, 기존 미식별 색인은 source_database_unbound로 중단합니다. 이관·재색인 실행은 자동으로 하지 않습니다.
- 파일 SQLite는 경로, MySQL은 자격 증명을 제외한 서버·DB 주소를 식별합니다. 같은 주소의 DB를 통째로 교체한 세대까지 감지하지는 않습니다. 메모리 SQLite는 Engine 수명 동안만 같은 식별값을 사용하며 다른 Engine과 공유하지 않습니다.
- 신문·단신 SQL의 확정 대표어를 유지합니다. 단신은 기존 수동 HS SQL 태그를 보존하지만 HS 집계·벡터 메타데이터에서는 제외합니다.
- 스케줄러의 신문·단신 스위치는 독립적인 기본 false입니다. 분류 후보 인덱스의 페이지 읽기·완료 검증·소유 버전만 정리하는 계약은 유지합니다.
- 소유 snapshot/제공자를 성공·오류·시간 초과 경로에서 종료하고 주입 제공자는 닫지 않습니다. 캐시는 새 요청에만 사용량을 기록합니다. 누락 usage와 보고된 0토큰을 구분합니다.
- HTTP·화면 전체 구현은 이 서비스 시험에 포함되지 않습니다. 실제 외부 모델 품질·RDS·운영 스케줄러·Windows 실환경은 별도 검증입니다.

## 후보 확보와 선택적 재순위화

`search(..., candidate_limit=40)`는 필터·차단·기사 중복 제거 후 고유 후보를 최대 40건 확보하지만, 반환은 요청의 `target_count`를 따릅니다. 후보 수만 늘릴 때는 원래 벡터 순위를 유지합니다. `candidate_pool` 실행 이벤트에 실제 후보를 기록합니다.

`search(..., candidate_limit=40, rerank='local_feedback')`는 기존 질문 유사도와 1위 기사의 평균 벡터에 대한 유사도를 각각 0.5로 결합합니다. 저장 벡터만 읽으며 추가 모델 호출은 없습니다. 첫 검색 결과가 잘못되면 그 오류를 강화할 수 있고, 직접 근거의 사실성을 검증하는 방식은 아닙니다. 기본값은 재순위화 비활성화입니다.

`RagService`에도 같은 `candidate_limit`, `rerank` 인자를 전달할 수 있습니다. 사용처별 최종 건수·조건·차단은 유지됩니다. 한 번에 후보 수 또는 재순위화 중 하나만 변경해 같은 Dev로 비교합니다.

`rerank=True`는 별도 외부 모델 방식입니다. 조건·차단을 통과한 기사만 평가하며, 직접 근거 점수에는 같은 기사 청크의 실제 발췌를 요구합니다. 형식·발췌 오류는 한 번 재요청하고 계속 실패하면 원래 검색 순서를 보존하며 실패를 응답에 표시합니다. 전체 마감 초과는 그대로 전달합니다. 외부 전송 범위 확인이 필요한 방식이며 로컬 재순위화 실험만으로 이 방식의 실제 품질이 검증됐다고 간주하지 않습니다.

모델 재순위화 출력 계약 v2는 배치의 각 후보 ID를 `scores` 객체의 필수 속성으로 지정합니다. 누락·추가 ID는 스키마와 실행 시 검증에서 모두 거부하며, 점수 기준·배치 크기·최종 반환 수는 유지합니다. 응답 계약 실패를 정상적인 검색 0건으로 집계하지 않습니다.

모델 재순위화 출력 계약 v5는 자유 발췌 대신 기사별 `evidence_index`를 선택합니다. 구간 번호의 최솟값·최댓값을 출력 스키마와 코드에서 검증하여 선택지 열거 한도와 다른 기사 ID 참조 오류를 피합니다. 원문 전체를 줄 단위(긴 줄은 최대 400자)로 빠짐없이 구간화하고 원래 청크·시작·끝 위치를 보존합니다. 직접 근거 점수에는 같은 기사 구간 ID가 필수이며, 원문에서 가져온 발췌를 정확 일치로 검증합니다. 이는 발췌 복사 오류를 방지하며, 선택한 구간이 점수를 의미적으로 뒷받침하는지는 별도 검수 대상입니다.

재순위화 v7은 복합 질문의 일부에 직접 답하는 원인·개선 조건을 평가하고, 포괄적 개념에 대한 구체적 현상 설명도 원문에 명시된 경우 부분 답변으로 평가하도록 지시합니다. 질문에서 요구한 발언 주체·범위는 유지합니다. 관련 정답 후보와 답변에 반드시 필요한 근거는 구분하며, Dev 정답 포함 여부를 모델 입력이나 점수 보정에 사용하지 않습니다. 발췌 구간 검증과 최종 반환 수는 변경하지 않습니다.
