# v26 기사 RAG

기존 `total_article` 벡터 색인을 재사용해 확정된 국가·품목·기간에 관한 기사 근거를 찾는다. 생성 모델은 `gpt-4.1-mini`이며 기사별로 전체 본문 관련성을 확인한 뒤 1~2문장과 문장별 원문 인용을 반환한다. 검색 결과가 질문 전체에 답한다는 보장은 하지 않는다.

## 요청과 처리 순서

1. 호출자가 국가·품목·기간·출처·차단 주제어를 확정해 `RagRequest`로 전달한다. 국가·품목은 질문의 대상이며, 다른 태그가 붙은 기사도 본문이 그 대상을 설명하면 후보가 된다. 검색 범위 모드 선택은 없다.
2. 한 요청 동안 같은 색인 세대와 사전을 사용한다. 기사 임베딩 모델과 같은 모델로 질문을 임베딩하고, 출처·발행기간·차단 조건을 적용한다. 국가·품목 명칭과 사전 표현은 검색 질의에 반영한다.
3. 챗봇은 현재 DB의 공개 여부·사용자 차단·기사 식별값·발행일을 후보 선정 전에 확인한다. 제외된 기사는 최종 건수와 요약 호출을 소비하지 않는다. 국가·품목 태그를 다시 강제 필터로 적용하지 않는다.
4. 후보의 원문은 동일 색인 세대의 `articles.jsonl`에서 복원한다. `catalog_path`가 있으면 정확히 같은 버전의 원문을 우선 읽는다. 별도 SQLite 카탈로그는 필수 파일이 아니다.
5. 기사 전체 본문으로 질문 대상 관련성을 판단한다. `unrelated`는 제외하고 `uncertain`은 불확실성을 기록한 채 후속 생성·문장 검수를 진행한다. 국가 간 명시적 비교는 필요하지 않다.
6. 질문에 직접 답하거나 구체적인 참고 근거가 되는 내용만 생성한다. 수치의 주체·기간·단위·조건과 인과·전망 표현을 검토하고, 각 문장을 원문 인용과 대조한다. 질문의 일부만 확인되면 확인된 부분만 반환한다.
7. 챗봇은 반환된 인용이 현재 DB 본문에도 존재하는지 다시 확인한다. 현재 제목·분류로 색인 헤더를 재구성하지 않는다. 출처·기사 ID·발행일 대조, URL 정규화 후 일치 확인, 답변과 인용의 대화 기록 저장을 유지한다.

## 최신순과 응답 형식

`order_by="published_date_desc"`도 같은 벡터 검색 경로를 사용한다. 기간은 요청 종료일을 포함한 최근 14일과 요청 기간의 교집합이다. 고유 기사 후보 최대 40건 안에서 발행일 내림차순으로 확인한다. 응답의 `latest_scope`, `latest_candidate_limit`, `latest_period`, `latest_scope_description`으로 **해당 후보 안의 최신순**임을 표시한다.

`answer_format="summary"`는 설명과 인용, `"list"`는 목록을 반환한다. 생략한 최신순 요청은 질문에서 표시 형식과 본문 주제를 판정한다. 국가·품목·주제가 있으면 목록도 본문 관련성을 확인한다. 주제와 대상 조건이 없는 순수 최신 목록은 본문 의미 검수를 하지 않으며 그 제한을 기사에 표시한다. 일반 유사도 검색의 기본 형식은 요약이다.

`unverified_question_parts`는 인용이 확인하지 못한 질문 구절이다. 응답 전체에는 반환 문장들이 공통으로 확인하지 못한 구절을 표시한다. 빈 목록은 완전한 답변이라는 판정이 아니다.

## 호출과 운영

```python
from backend.app.agents.tools import search_news
from backend.app.schemas.rag import RagRequest

request = RagRequest.model_validate(request_json)
response = search_news(request)
```

`request_json`의 공개 가능한 합성 예시는 [contract_examples.json](contract_examples.json)에 있다. 호출 전 국가·품목·기간을 확정해야 한다. `NEWS_VECTOR_ROOT`는 게시된 색인 루트, `OPENAI_API_KEY`는 모델 연결에 사용한다. 유사도와 최신순 모두 질의 임베딩이 필요하다. 운영 색인 재생성은 RAG 코드 반영의 필수 단계가 아니다.

`create_service(api, root=..., catalog_path=...)`와 `ExecutionContext(deadline=...)`를 사용하면 호출자가 모델 연결과 전체 실행 기한을 관리할 수 있다. 기존 챗봇 도구의 전체 기한은 15초다. 시간 초과·연결·색인 오류는 저장 가능한 실패 안내로 처리하며 미검수 DB 발췌로 답변을 대체하지 않는다. 이 기한에서 실제 GPT 응답이 완료되는지는 운영 환경에서 별도 측정해야 한다.

요약 캐시는 질문·조건·본문 버전·모델·프롬프트·본문 검색 정책을 구분한다. 로그와 캐시에는 기사 정보가 포함될 수 있으므로 운영 데이터 디렉터리는 커밋 대상에 포함하지 않는다.

## 평가·문항 작성·검수 도구

| 모듈 | 용도 | 실행 진입점 |
|---|---|---|
| `evaluation_drafts` | 기사 자료에서 검수 대기 문항 초안 작성 | `python -m backend.app.services.rag.evaluation_drafts --help` |
| `review` | 원문과 사람의 명시적인 검수 결과를 대조해 정답셋 확정 | `python -m backend.app.services.rag.review --help` |
| `evaluate_service` | 고정 색인과 문항으로 검색 결과 기록 | `python -m backend.app.services.rag.evaluate_service --help` |
| `evaluation` | 기록된 결과와 정답셋의 기사 단위 지표 집계 | `python -m backend.app.services.rag.evaluation --help` |
| `baseline` | 명시적으로 제공된 코드 스냅샷으로 비교용 기준선 재구성 | `python -m backend.app.services.rag.baseline --help` |

합성 평가 입력은 [evaluation_example.json](evaluation_example.json)에 있다. 현재 평가 입력은 `rag-evaluation-v2`다. 검색 기록과 채점은 생성 답변의 의미 검수와 별개다. `evaluate_service` 실행·기준선 임베딩·기존 검색 기록에는 모델 호출 비용이 발생할 수 있고, `--help`와 저장 결과 채점에는 모델 호출이 없다.

Holdout은 설정 확정 후 최종 검증에만 사용한다. 검수 전 문항을 정답셋으로 간주하지 않으며, 데이터셋 지문과 색인 세대를 고정해 결과를 대조한다. 문항 수와 지표의 합격선을 자동으로 강제하지 않는다. 저장 결과의 지표와 오류·미실행·미판정 문항 수를 집계하며 합격·불합격 판정은 반환하지 않는다. 초안 작성 시 `--dev-count`와 `--final-count`로 확정한 문항 수를 지정한다.

## 로컬 검증

```sh
python -m unittest discover backend/tests -p 'test_rag*.py'
python -m unittest backend.tests.test_pr43_followup
python -m unittest discover backend/tests
python -m pytest backend/tests/test_news_index_migrate.py backend/tests/test_news_index_catalog.py backend/tests/trade_news/test_common_news_vector.py backend/tests/trade_news/test_vector_resource_contract.py
```

합성 기사·모델 대역·임시 SQLite/Chroma로 실행한다. 실제 GPT로 새 답변을 생성하거나 Holdout을 재평가하는 검사가 아니다.
