# 기존 본문 Chroma의 일회성 세대 이관

`news_embed`의 기본 본문 경로는 `NEWS_VECTOR_ROOT` 또는 저장소의 `data/rag/index`입니다. 활성 세대는 `active.json`이 가리키며 그 세대의 `chroma/kotra_article`, `articles.jsonl`, `vocabulary.json`, `status.json`을 함께 사용합니다. 공통 키워드는 같은 root의 `keywords/news_keyword`이고 단신 분류 후보는 별도 `chroma_data/short_news_keywords_<버전>`입니다.

구형 `chroma_data/kotra_article`은 자동으로 삭제하거나 새 경로로 간주하지 않습니다. `legacy_direct_index_migration_required`는 구형 본문이 있는 상태에서 새 세대가 준비되지 않았다는 뜻입니다. 아래 명령은 본문 벡터를 복사하며 OpenAI를 호출하지 않습니다. `news_keyword`나 단신 분류 후보 컬렉션은 이관하지 않고 원본 위치에 그대로 둡니다. 공통 키워드는 이후 정상 색인 실행에서 DB 전체 태그를 기준으로 갱신합니다.

## 이관 입력과 사전 확인

1. 원본 Chroma를 쓰는 모든 작업을 중지하고, 같은 시점의 기사·태그·정제 본문·사전 자료를 확보합니다. 원본이 갱신되는 상태의 온라인 복제는 지원하지 않습니다.
2. 원본 디렉터리, 대상 root, 임시 디렉터리는 서로 겹치지 않게 지정합니다. 심볼릭 링크는 허용하지 않습니다. 원본 전체 임시 복제본과 새 세대가 들어갈 디스크 공간이 필요합니다.
3. 운영 담당자가 DB와 원본을 대조해 아래 UTF-8 JSON 카탈로그를 준비합니다. 카탈로그에는 원문이 포함되므로 저장소의 `data/` 등 비공개 실행 경로에 두고 커밋하지 않습니다.
4. `database_id`는 `backend.app.db.news_lock.database_identity`/`engine_identity`와 같은 64자리 해시입니다. 사용자명·비밀번호를 저장하지 않습니다. 카탈로그의 해시와 CLI 해시가 같아야 하며, 기존 대상의 DB 바인딩이 다르면 중단합니다. 해시는 운영 담당자가 카탈로그의 실제 출처 DB와 대조해야 합니다. 이관 CLI(`news_index_migrate`) 자체는 DB에 연결하지 않습니다. 아래 카탈로그 생성 CLI는 DB를 조회합니다.

## 카탈로그 생성 CLI

기사·태그·HS·국가 테이블과 관리자 `visibility_status` 컬럼을 먼저 준비합니다. 다음 명령은 DB를 조회하고 JSON 파일만 작성하며 DB 행·Chroma·스케줄러는 변경하지 않습니다. DB 설정에는 조회 계정을 사용할 수 있습니다. 같은 논리 DB라면 조회 계정과 적재 계정의 `database_id`는 같습니다. 실제 접속 URL·비밀번호는 출력하지 않습니다.

```bash
python -m backend.app.scheduler.jobs.news_index_catalog \
  --source KOTRA_MARKET \
  --raw "<검증할 news_raw CSV 경로>" \
  --dictionary-dir "<synonyms.csv·hypernyms.csv·topic_categories.csv 디렉터리>" \
  --output data/news/migrations/catalog.json \
  --env "<조회용 MYSQL_* 환경 파일 경로>"
```

- `--raw`는 여러 번 지정할 수 있습니다. 생략하면 `--raw-dir`(기본 `data/news`)의 `news_raw_*.csv` 전체를 읽습니다. 최신 파일 하나로 제한하지 않습니다.
- `--source`는 반복 지정하며 기본은 `KOTRA_MARKET`입니다. 구형 컬렉션에 단신도 있다면 `--source KOTRA_SHORT_NEWS`를 함께 지정합니다. 선택한 출처의 VISIBLE 기사 전체를 내보내며, 이관 단계에서 컬렉션의 전체 ID·개수와 다시 대조합니다. 숨김 기사를 VISIBLE로 간주하거나 컬렉션의 누락 기사를 조용히 제외하지 않습니다.
- 동일 기사 ID의 CSV 행을 마지막 행으로 덮어쓰지 않습니다. 정제·마스킹한 전체 본문이 DB와 정확히 일치하고 실제 `newsBdt`가 있는 후보를 선택합니다. 같은 본문인데 원본 키워드가 충돌하면 중단합니다. 동일한 후보의 반복은 하나의 근거로 취급합니다. `kwrd` 컬럼 자체가 없으면 원본 키워드는 미확인 상태로 남깁니다.
- 관리자 공개 상태 확인, 검증된 MARKET 본문, 필수 사전 파일이 없으면 출력하지 않습니다. 본문이 없어 요약·제목으로 채운 행을 완전한 본문으로 추정하지 않습니다.
- MySQL은 하나의 REPEATABLE READ 연결/트랜잭션에서 조회합니다. 원본 CSV와 사전은 읽기 전후 SHA-256을 확인하므로 내보내는 동안 해당 파일을 변경하지 않습니다. 파일 집합과 DB가 같은 운영 시점인지 확인하는 절차를 대신하지 않습니다.
- 출력 기본값은 Git에서 제외되는 `data/news/migrations/catalog.json`입니다. 검증을 모두 마친 JSON만 원자적으로 게시합니다. 검증 중 오류가 나면 기존 출력 파일을 보존하며, 입력 파일을 출력으로 덮어쓰는 것은 거부합니다.
- 성공 JSON의 `database_id`를 아래 이관 명령에 사용합니다. 카탈로그 파일에는 본문이 있으므로 커밋하지 않습니다. 출력에 운영 본문을 표시하지 않습니다.

카탈로그 형식(값은 합성 예시):

```json
{
  "version": 1,
  "database_id": "<검증된 DB 식별 SHA-256>",
  "vocabulary": {
    "products": {}, "countries": {}, "synonyms": {}, "hypernyms": {}, "categories": {}
  },
  "articles": [{
    "article_id": 1,
    "source_code": "KOTRA_MARKET",
    "external_id": "synthetic-1",
    "title": "합성 기사",
    "published_date": "2026-10-01",
    "source_url": "https://example.org/1",
    "body": "검증된 정제 본문",
    "countries": ["KR"],
    "kw_topic": ["수출"],
    "keywords_ready": true,
    "metadata_missing": ["original_kw_sub"],
    "provenance": [],
    "visibility_status": "VISIBLE"
  }]
}
```

빈 사전은 형식 설명용입니다. 운영에서는 실제 검증된 사전 스냅샷을 사용해야 합니다. 다음 정상 갱신의 사전과 다르면 기존 타 출처를 보존하는 사전 변경 보호 규칙에 따라 중단될 수 있습니다.

카탈로그 기사 항목은 `ArticleDocument` 계약입니다. 구체어·HS·분류 등 확인된 추가 필드는 그 계약에 맞게 포함합니다. 단신은 HS 품목을 넣지 않습니다. 원본 키워드를 모르면 `raw_keywords`는 null/생략하고 `metadata_missing`에 `original_kw_sub`를 표시합니다. `body_origin`이 미확인인 본문은 길이·제목으로 추정하지 않고 이관을 중단합니다. URL이 없으면 빈 문자열과 `metadata_missing: ["source_url", ...]`로 명시해야 합니다.

CLI 내부와 같은 방식으로 카탈로그를 준비할 때는 실제 SQL 입력 어댑터 `rag.ingest.read_articles`와 `Vocabulary.load`를 사용할 수 있습니다. MARKET의 본문 출처를 확인하는 `market_supplements`에는 DB 본문과 일치하는 검증된 수집 원본이 필요합니다. `read_articles`의 반환 기사와 다음 사전 구조를 저장합니다.

```python
articles, vocabulary = read_articles(engine, confirmed_sources,
    vocabulary=Vocabulary.load(), supplements=verified_supplements)
exported_vocabulary = {
    "products": vocabulary.products, "countries": vocabulary.countries,
    "synonyms": vocabulary.synonyms, "hypernyms": vocabulary.hypernyms,
    "categories": {t: vocabulary.categories.lookup(t) for t in vocabulary.categories.listed},
}
# 실제 DB에서 VISIBLE임을 확인한 기사만 visibility_status=VISIBLE로 명시합니다.
# 원본 컬렉션의 기사 전체와 일치하는지 확인한 뒤 카탈로그로 저장합니다.
```

이 코드는 입력 준비 방법을 설명하는 조각이며 여기서 운영 DB에 실행하지 않았습니다. `confirmed_sources`는 컬렉션 이름으로 추정하지 않습니다. 원본 출처·내부 기사 ID·외부 ID·조각 번호·전체 문서와 카탈로그를 대조합니다. 메타데이터에서 출처 등이 빠진 경우에도 해당 기사 ID와 문서가 정확히 일치하는 명시적 카탈로그만 보완 근거로 사용합니다. 숨김 기사, 누락 기사, 중복 내부 ID, 태그·본문 불일치는 조용히 제외하지 않고 전체 이관을 중단합니다.

지원 형식은 기존 `build_docs`의 `article_id:chunk` ID와 `news-header-v1` 문서, 1000자/150자 겹침 계약입니다. 기존 문서가 이 계약과 다르면 벡터의 의미를 추측해서 새 문서에 붙이지 않습니다. 원본 모델 정보가 없으면 근거를 확인한 후에만 `--legacy-model text-embedding-3-large`를 명시합니다. 다른 모델·차원, 미확인 거리 설정은 중단합니다. 이관하는 각 벡터의 3072차원·유한값·비영벡터를 검사합니다.

## 국가 순서 호환과 다음 갱신

DB의 정렬된 국가 목록과 구형 문서의 국가 순서만 다른 경우를 허용합니다. 구형 국가 메타데이터의 구성·개수·중복을 검사하고 그 순서로 전체 문서를 다시 구성하여 비교합니다. 날짜·제목·분류·본문·조각 경계는 완전히 같아야 합니다. 같은 기사 내 조각들의 국가 순서도 일치해야 합니다.

입력 카탈로그 파일은 수정하지 않습니다. 새 세대에는 구형 문서와 일치하는 국가 순서의 카탈로그·payload·content_version 및 실제 문서 `text_hash`를 함께 저장합니다. 이관 시 문서·벡터를 바꾸거나 모델을 호출하지 않습니다. `country_order_preserved_articles`는 입력과 다른 구형 국가 순서를 보존한 기사 수입니다.

다른 출처를 갱신하면 이전 카탈로그의 구형 표현을 보존합니다. 해당 출처를 DB에서 정상 갱신할 때만 현재 정렬된 국가 순서로 전환하며 문서가 바뀐 조각을 임베딩합니다. 그 다음 동일 입력은 벡터를 재사용합니다. 이 본문 조각 수와 새 전역 키워드의 임베딩 수는 별도로 확인합니다. 특정 운영 조각 수를 미리 보장하지 않습니다.

## 명령

저장소 루트에서 실행합니다. 대상 root를 생략하면 `NEWS_VECTOR_ROOT`, 없으면 `data/rag/index`입니다.

```bash
# 계획·전체 레코드 검증: target root/DB 식별 파일/lock 파일을 만들지 않습니다.
python -m backend.app.scheduler.jobs.news_index_migrate \
  --source "<구형 chroma_data 절대경로>" \
  --root "<새 본문 root 절대경로>" \
  --catalog "<검증된 UTF-8 카탈로그.json>" \
  --database-id "<DB 식별 SHA-256>" --source-stopped

# 위 검증이 통과한 동일 입력을 실제 복사·게시
python -m backend.app.scheduler.jobs.news_index_migrate \
  --source "<구형 chroma_data 절대경로>" \
  --root "<새 본문 root 절대경로>" \
  --catalog "<검증된 UTF-8 카탈로그.json>" \
  --database-id "<DB 식별 SHA-256>" --source-stopped --apply
```

`--batch-size` 기본값은 256이고 1~1000을 지원합니다. 벡터는 배치 단위로 복사·대조하며 모델 호출은 없습니다. 기사 카탈로그와 예상 문서 목록은 메모리에 유지합니다.

## 원본 보존·복구

- 원본 경로에는 Chroma 클라이언트를 연결하지 않습니다. SQLite WAL/SHM과 HNSW 파일을 포함한 전체 디렉터리를 복제합니다. 파일 내용·목록의 SHA-256을 복제 전후·게시 직전에 확인하고 임시 복제본에서만 Chroma를 엽니다. 원본 파일·컬렉션을 수정·삭제하지 않습니다. dry-run도 임시 복제본을 만들며 종료 시 정리합니다. 이는 writer 중지를 대신하는 분산 스냅샷 기능은 아닙니다.
- 적용 시 새 대상 root와 `publish.lock`이 생성될 수 있습니다. 검증 실패 시 기존 `active.json`과 DB 식별 기록은 보존합니다. 별도 활성 세대가 이미 있으면 덮어쓰지 않습니다.
- 모든 ID·문서·벡터·기존 메타데이터를 검증하고 필수 메타데이터만 보완합니다. 카탈로그와 사전을 저장하고 소유 클라이언트를 닫은 뒤 `active.json`을 원자적으로 게시합니다.
- 같은 입력의 재실행은 활성 상태·컬렉션 계약과 모든 레코드를 다시 대조해 `already_complete`를 반환합니다. 이관 뒤 정상 갱신으로 다른 세대가 활성화됐다면 이관 CLI는 중단합니다. 정상 갱신을 되돌리지 않습니다.
- 중단된 복사의 미게시 `building`/`failed` 폴더는 진단용으로 남습니다. 재실행은 새 세대에 **처음부터 복사**하며 부분 배치를 이어 붙이지 않습니다. 게시 전 실패·종료 오류는 기존 활성 상태에 영향을 주지 않습니다. DB 식별 기록 저장 뒤 프로세스가 강제 종료되면 같은 DB의 바인딩과 미게시 세대가 남을 수 있고, 같은 입력으로 재시도할 수 있습니다.
- 이관된 ID는 출처·기사 ID·조각·실제 문서가 같은 경우 다음 증분 갱신에서도 보존하고 벡터를 재사용합니다. 문서가 바뀌면 정상 갱신에서 해당 조각을 새로 임베딩합니다. 다른 출처는 기존 카탈로그와 벡터를 보존합니다.
- 실제 운영 이관, 구형 Chroma 버전별 호환성, 실제 Windows 및 MySQL/RDS는 이번 로컬 합성 시험으로 검증한 것으로 간주하지 않습니다. 팀원이 보고한 운영 기사·조각 수는 운영 조회로 확인한 수치가 아닙니다.

## 관리자 필드 참고

`first_ingested_at`은 최초 수신 시각이며, `admin_meta_json.classification_input`은 과거 분류 결과 자체가 아니라 `snapshot_ref`와 `sha256`으로 정책 코드·사전·DB 분류 스냅샷을 가리키는 JSON입니다. 신규 INSERT는 현재 적재 시각과 현재 정책 스냅샷을 채우지만 기존 UPDATE는 해당 관리자 값을 바꾸지 않습니다.

기존 MARKET 행의 최초 시각·당시 정책을 확인할 근거가 없으면 현재 시각이나 현재 정책으로 소급 채우지 않습니다. 기존 값은 보존하고 미확인 상태는 설명·보고서에 명시합니다. `unknown` 문자열을 DB에 넣지 않습니다. 기존 CHECK가 허용하는 JSON null과 키 누락은 다르며 신규 INSERT 트리거는 정책 객체를 요구합니다. 현재 집계·본문 색인의 입력은 이 두 값이 반드시 채워져 있어야 동작하는 구조가 아닙니다. backfill은 증거·정책·대상 범위를 별도로 승인한 뒤 수행해야 하며 이관 CLI는 DB backfill을 하지 않습니다.
