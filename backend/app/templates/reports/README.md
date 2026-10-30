# 리포트 HTML 템플릿 (정기·요청형)

`리포트_양식_최신판.pdf`의 월간·주간·분기·요청형 양식을 Jinja2 HTML로 옮긴 템플릿입니다. 수치·문장·기사·목록은 모두 컨텍스트 변수로 받고,
템플릿에는 고정 문구(제목 틀·계산식 설명·면책 문구)만 있습니다. A4 한 쪽이 `<section class="page">` 하나이며, 그래프는 JavaScript 없는
SVG라 HTML → PDF 변환에서도 그대로 나옵니다.

| 파일 | 내용 |
|---|---|
| `monthly.html` | 월간 (`RPT_MONTHLY`): 표지 · 목차 · 이달의 요약 · 관심 품목별 상세(품목 수만큼) · 현지 이슈 · 데이터 기준 |
| `quarterly.html` | 분기 (`RPT_QUARTERLY`): 표지 · 목차 · 분기 요약 · 분기·월별 추이 · 관심 국가 추이(8개 분기 / 3개월) · 주요 수출국 · 현지 이슈 · 데이터 기준 |
| `weekly.html` | 주간 (`RPT_WEEKLY`): 표지 · 목차 · 이번 주 요약 · 관심 품목·국가 기사 · 급증 키워드·기사 분포 (수출 통계 없음) |
| `ondemand.html` | 요청형 (`RPT_ON_DEMAND`): 표지 · 목차 · 핵심 요약 · 월별 수출 추이 · 주요 수출국 · 현지 이슈 · 데이터 기준 (고른 섹션만, 아래 「요청형」) |
| `_macros.html` | 공통 부품: 쪽 틀(머리말·꼬리말) · 표지 · 목차 · KPI 카드 · 요약 문장 · 기사 카드 · 출처 표 · 범례 |
| `report.css` | 양식 디자인 (강조색 `#006849`, `layout.json`과 같음) |
| `samples/*.json` | 양식 PDF 수치로 만든 예시 컨텍스트 (미리보기·테스트용) |
| `layout.json`, `summary.docx` | 기존 리포트 생성(`api/reports.py`, `api/files.py`)이 쓰는 파일. 이 템플릿과 별개 |

렌더 코드: `backend/app/services/report_templates.py` (`render(report_type, context)`). 빠진 변수는 `StrictUndefined`로 오류가 납니다.

```powershell
# 예시 데이터로 미리보기 (tmp/는 git 제외)
python -m backend.app.services.report_templates monthly --out tmp/report_preview/monthly.html
python -m backend.app.services.report_templates quarterly --out tmp/report_preview/quarterly.html
python -m backend.app.services.report_templates weekly --out tmp/report_preview/weekly.html
python -m backend.app.services.report_templates ondemand --out tmp/report_preview/ondemand.html
# 다른 데이터로: --data 경로/컨텍스트.json

python -m unittest backend/tests/test_report_templates.py
```

## 표시 규칙

- 금액은 결과표의 **USD 원값**을 그대로 넘깁니다. 템플릿이 `musd` 필터로 백만 달러로 바꿉니다 (879000000 → `879.0`).
- 증감률은 % 숫자(18.5, −14.8)를 넘기면 `change` 필터가 `▲ 18.5%`(초록) / `▼ 14.8%`(빨강)으로, `null`이면 `—`로 표시합니다.
  비교 값이 0이라 계산할 수 없는 국가는 행에 `is_new_mom`/`is_new_yoy: true`를 주면 “신규”로 표시합니다.
- 요약 문장의 `**강조**`는 굵은 강조색이 됩니다. 그 밖의 문자열은 모두 HTML 이스케이프됩니다.
- 날짜는 ISO 문자열(`2025-12-15`, `2026-01-16T06:00:00`)로 넘기면 `2025.12.15`, `2026.01.16 06:00`으로 표시합니다.
- 관심 국가 선 색상은 `conditions.countries` 순서로 정해져 같은 리포트의 모든 그래프에서 같습니다.
- 꺾은선 끝의 마지막 값 라벨은 그래프 영역 안에서 겹치지 않게 펼칩니다. 국가가 많아 자리가 없으면(월간 7개·분기 17개 초과)
  라벨을 그리지 않고 범례에 “국가 171.2”처럼 마지막 값을 붙입니다 (`end_labels_fit`). 마지막 기간 값이 `null`이면
  값이 있는 마지막 기간을 연도까지 붙여 “국가 10.0(24.4Q)”처럼 표시합니다. 마지막 기간 여부는 라벨 글자가 아니라 위치로 판단합니다
  (축 라벨은 `24.1Q, 2Q, …`처럼 연도가 바뀔 때만 연도를 붙이므로 `4Q`가 연도마다 반복됨).
- 주요 수출국 표는 상위 10개국까지만 표시합니다(나머지는 `others`). 월간·분기 KPI 카드는 앞의 품목 2개 + `summary.highlight`입니다.
- 목록 길이는 자유입니다: 관심 품목 N개 → 월간 상세 N개(관심 국가가 많으면 품목마다 “(계속)” 쪽 추가), 분기 그래프는 2개씩 한 쪽.
- 쪽 나눔은 `report_templates.layout()`이 글자 수로 블록 높이를 추정해 정합니다. 요약(문장·품목 표·관심 국가 표·확인할 점)·기사·
  월간 상세(주요 수출국 표)·주간 이슈·일정·국가별 기사 수·급증 키워드·단신이 한 쪽을 넘으면 “(계속)” 쪽으로 넘어갑니다.
  표·그래프는 행 단위로 나뉘어 머리글·범례가 다시 나오고, 요약 문장 번호와 막대 길이 기준은 쪽이 나뉘어도 이어집니다.
  관심 국가 표는 가로로 넘치지 않게 품목 3개씩 나누며, 분기 관심 국가 추이는 범례가 길면 한 쪽에 품목 1개만 둡니다.
  쪽 번호와 목차는 자동 계산되며, 빈 목록은 “관련 기사 없음” 등 안내 문구가 나옵니다.
  높이는 한글 1자 = 1em(Noto Sans KR)으로 계산해, 폭이 좁은 글꼴(맑은 고딕)에서는 쪽 아래 여백이 조금 남습니다.
- 꺾은선 값(`trend`, `country_trend`, `country_quarters`, `country_months`)이 `null`인 달은 0으로 그리지 않고 선·면적을 끊습니다.
  앞뒤가 모두 비어 있는 값은 점으로 표시합니다. 막대그래프(`quarters`)의 `null`은 그 막대만 비웁니다. 실제 값 0은 그대로 그립니다.
- `report.is_sample: true`면 꼬리말·기사 카드에 “템플릿 예시” 표시가 붙습니다. 실제 발행은 `false`.

## 미정·미준비 값 (`null` 허용)

키는 항상 넘기되, 값이 정해지지 않았으면 `null`(또는 빈 목록)로 넘깁니다.

| 상황 | 변수 | 표시 |
|---|---|---|
| 팀 기준 미정 → 블록 숨김 | `summary.highlight` | 세 번째 KPI 카드 생략 (품목 카드만) |
| | `summary.sentences` | 요약 문장 상자(주간은 “이번 주 핵심”) 생략 |
| | `summary.check_points`, 주간 `summary.schedule` | 표 생략 |
| | 주간 `summary.spike_ratio` (`counts.spike_keywords`) | 급증 키워드 카드·그래프 “급증” 배지·기준 문구 생략 |
| | 주간 `summary.issue_spike_ratio` (선택) | 주요 이슈 기준 문구에서 배수 생략. 관심 키워드 기준(`spike_ratio`)과 따로 정합니다 |
| 문장 검증 전 | `verification` | “문장 속 수치 검증 전” (검증 통과로 표시하지 않음) |
| 통계 미준비 | `trend`·`country_trend`·`quarters` 등 값이 전부 `null` | 그래프 자리에 “데이터 준비 중” |
| | `top_countries`, `top5_compare`, `interest_overview` 빈 목록 | 표·그래프에 “데이터 준비 중” (관심 국가가 없으면 “관심 국가가 없습니다.”) |
| | 개별 수치 `null` | `—` |

## 컨텍스트 변수

정기 세 종류 공통 (요청형은 아래 「요청형」 절):

| 변수 | 예시 | 설명 |
|---|---|---|
| `report.report_id` | `RPT-MO-202512-U0381` | 리포트 ID (머리말·목차·출처) |
| `report.report_type` | `RPT_MONTHLY` | `service_option` REPORT_TYPE 코드 |
| `report.kind_label` | `정기 리포트 · 월간` | 머리말 오른쪽 |
| `report.title` / `period_label` | `관심 품목 월간 수출 동향` / `2025년 12월` | 표지·목차 제목 |
| `report.cover_eyebrow` / `cover_lead` | `정기 리포트 · 월간 MONTHLY` / `관심 품목 2개 · …` | 표지 |
| `report.footer_label` | `관심 품목 월간 수출 동향 · 2025년 12월 · 정기(월간)` | 꼬리말 왼쪽 |
| `report.published_at` | `2026-01-16T06:00:00` | 발행 시각 (KST) |
| `report.is_sample` | `false` | 예시 표시 여부 |
| `subscription.label` | `관심 품목 2 · 관심 국가 3 (마이페이지 리포트 구독 · 월간)` | 보고서 정보 · 구독 조건 |
| `conditions.hs_items[]` | `{hs_code, hs_ver, name, label, display}` | 관심 품목. `label`=짧은 이름(기초화장품), `display`=보고서 정보 표기 |
| `conditions.countries[]` | `{country_code, name}` | 관심 국가 (그래프 색상 순서) |
| `meta.data_basis` | `관세청 수출입무역통계 · KOTRA …` | 보고서 정보 · 데이터 기준 |
| `articles[]` | `{no, country, item_label, published_date, source_label, office, title, summary, source_url}` | 현지 이슈 기사. `office`·`source_url`은 없으면 `null` |
| `article_limit` | `2` | 국가당 최대 기사 수. `null`이면 “제한 해제” |

월간·분기 공통:

| 변수 | 설명 |
|---|---|
| `meta.publish_note` | 발행 조건 문구 (`12월 통계 적재·검증 완료 후 자동 발행`) |
| `verification.passed` / `total` | 문장 속 수치 검증 통과 수 (검증 전이면 `verification: null`) |
| `summary.highlight` | 세 번째 KPI 카드 `{label, value, sub}` (미정이면 `null`) |
| `summary.sentences[]` | 요약 문장 (번호 자동, `**강조**`) |
| `summary.check_points[]` | 확인할 점 `{target, content}` (빈 목록이면 표 생략) |
| `items[]` | 관심 품목별 결과 (아래) |
| `interest_overview[]` | 관심 국가 한눈에 `{name, values: [{amount_usd, yoy_pct}] }` — `values`는 `items` 순서 |
| `sources[]` | 출처 표 `{label, provider, scope}` |

월간 `conditions`: `target_month`(`2025-12`), `prev_month`, `prev_year_month`. `meta.latest_available_month`와 같으면 “(최신 공개 월)” 표시.
월간 `items[]`: `hs_code, name, label, amount_usd, mom_pct, yoy_pct, country_count, country_count_diff, top_country{name, share_pct}`
(`country_count_diff` = 전년 동월 대비 수출국 수 증감, 분기는 전년 동분기 대비),
`trend{range_label, labels[13], current[13], previous[13]}`(USD), `country_trend[{country_code, name, values[13]}]`,
`top_countries[{rank, name, is_interest, amount_usd, share_pct, mom_pct, yoy_pct}]`, `others{amount_usd, share_pct, mom_pct, yoy_pct}`(없으면 `null`).

분기 `conditions`: `year`/`prev_year`(`2025`/`2024`, 표·범례의 연도 표기), `quarter_label`(`4분기`), `months_label`(`10~12월`), `prev_year_quarter_label`, `prev_quarter_label`.
분기 `items[]`: 월간 항목 중 `mom_pct` 대신 `qoq_pct`, `prev_amount_usd`(전년 동분기), `quarters{labels[8], values[8], current_from}`(해당 연도 시작 위치),
`months[{label, amount_usd, prev_amount_usd, yoy_pct}]`(3개월), `country_quarters[{country_code, name, values[8]}]`,
`country_months{labels[3], series[{country_code, name, values[3]}]}`, `top_countries`(`mom_pct` 없음), `others`, `top5_compare[{name, current, previous}]`.
분기 `one_off_note`: 일회성 가능 판정 안내 문구 (`null`이면 생략).

주간: `conditions.week_label`, `conditions.compare_label`,
`summary{counts{total, market, short, spike_keywords}, total_note, spike_ratio, issue_spike_ratio?, sentences[], issues[{kind: SPIKE|REGULATION, title, country, basis, related}], schedule[{date, content, related}]}`,
`country_counts[{country_code, name, is_interest, market, short}]`,
`keyword_spikes[{keyword, country, this_week, weekly_avg_8w, ratio}]`(`ratio ≥ spike_ratio`면 “급증”),
`short_news{total, items[{published_date, country, content}]}`.

## 요청형 (`RPT_ON_DEMAND`)

사용자가 고른 조건(품목·국가·기간·비교·섹션)으로 만드는 리포트입니다. 이양원님과 합의한 표시 규칙(#45):

| 항목 | 규칙 |
|---|---|
| 품목 | 선택 품목을 **합산**한 결과 하나. 제목은 1개면 「기초화장품 수출 시장 분석」, 여러 개면 「기초화장품 외 2개 품목 …」, 없으면 「전체 품목 …」. 보고서 정보에는 실제 선택 목록 전체 |
| 섹션 | `SUMMARY`(핵심 요약)는 항상. `KPI` → 주요 지표 카드 8개 + 월별 수출 추이 쪽, `TOP_COUNTRIES` → 주요 수출국 쪽, `LOCAL_ISSUES` → 현지 이슈 쪽. 고르지 않은 섹션은 목차·쪽 번호·포함 항목에서 빠짐 |
| `GROWTH_RISK` | 증가율 순위의 최소 규모 기준이 정해질 때까지 **골라도 섹션 전체를 숨김** (예측 문구 포함). 기준이 정해지면 양식 6쪽을 추가 |
| 비교 | 백엔드가 확정한 분석·비교 기간을 그대로 표시. `NONE`이면 비교 열·비교 선·순위 변동을 숨김. 비교를 골랐지만 비교 기간 통계가 없으면(`status`≠`READY`) “비교 데이터 준비 중”으로 NONE과 구분. `CUSTOM`은 월별로 짝짓지 않고(`monthly_paired: false`) 기간 합계만 비교 |
| 요청 경로 | 질문 원문은 넣지 않음. `request_channel`이 `CHAT`이면 「AI 분석 대화에서 요청」, 그 밖은 「리포트 요청 화면」 |
| 요청 지표 | `metric`은 계산·정렬에 쓰지 않으므로 「요청 지표 ○○ · 현재 계산·정렬 기준: 수출액」으로 구분 표시 |
| 금액 | USD 원값을 받아 템플릿에서만 **억 달러**로 표시 (`eok` 필터: 8640000000 → `86.4`·`86.40`). 정기는 백만 달러 유지 |
| 숨김 | 검토 포인트·요약 문장·읽는 법·집중도 설명은 값이 없으면(null·빈 목록) 생략. 수치와 기사 연결 요약(RAG)은 이번 템플릿에 없음 |

| 변수 | 설명 |
|---|---|
| `report{report_id, report_type, kind_label, published_at, elapsed_seconds?, is_sample}` | 제목·표지 문구는 템플릿이 품목·기간으로 만듭니다 |
| `conditions.hs_items[]` | `{hs_code, hs_ver, name, label, display}` (빈 목록 = 전체 품목) |
| `conditions.countries[]` | `{country_code, name}` (빈 목록 = 전체 국가) |
| `conditions.period` | `{start, end, label: '2025.01 ~ 2025.12', months: 12, title_label: '2025년 연간', short: '2025'}` |
| `conditions.compare` | `{mode: NONE·MOM·YOY·YOY_PERIOD·CUSTOM, status: READY·NOT_READY, period_label, basis_label: '전년 같은 기간 자동 설정', short: '2024', monthly_paired}` |
| `conditions.sections[]` | 사용자가 고른 섹션 코드 (`KPI`·`TOP_COUNTRIES`·`GROWTH_RISK`·`LOCAL_ISSUES`) |
| `conditions.request_channel`, `conditions.metric` | `CHAT`·`FORM` / `{code, label}` |
| `meta.data_basis`, `verification` | 정기와 같음 (`verification: null`이면 “검증 전”) |
| `summary.kpi` | `amount_usd, prev_amount_usd, change_usd, change_pct, country_count, prev_country_count, country_count_diff, top1_name, top1_share_pct, prev_top1_name, prev_top1_share_pct, import_usd, import_change_pct, trade_balance_usd, top3_share_pct, prev_top3_share_pct, monthly_avg_usd, peak_month_label, peak_month_usd` (모르는 값은 `null` → `—`) |
| `summary.sentences[]`, `summary.check_points[{target, content, basis}]` | 요약 문장 · 검토 포인트 (null이면 생략) |
| `monthly` | `{labels[], current[], previous[], rows[{label, amount_usd, prev_amount_usd, change_usd, change_pct}], quarters[{label, amount_usd, prev_amount_usd, change_pct}], notes[]}` — 월 수 자유(14행 넘으면 표를 쪽으로 나눔), `quarters`는 완전한 분기만 |
| `top_countries[]` | `{rank, rank_move(정수·0·'NEW'·null), name, country_code, amount_usd, share_pct, prev_amount_usd, change_pct, is_new?}` (상위 10개) · `others` (없으면 `null`) |
| `concentration` | `{top1, top3, top10, countries}` 각 `{current, previous}` + `note?` |
| `articles[]`, `article_limit` | 정기와 같음 |
| `sources[]` | `{label, provider, scope, used_in}` (데이터 기준 쪽 표, 「이 리포트에서 쓴 곳」 칸 포함) |

## 기존 리포트 생성과의 관계

`api/reports.py::generate`는 정기 3종과 요청형에 `services/report_context.py::build_context`를 호출합니다.
실제 조회 값으로 구성한 컨텍스트를 `report.content_json.template_pdf`에 버전과 함께 저장하고,
`api/files.py::report_pdf` → `services/report_pdf.py::template_pdf`에서 이 HTML을 Chromium으로 PDF 변환합니다.
요청형 PDF도 HTML 템플릿으로 생성하며, 템플릿 컨텍스트가 없는 과거 문서의 ReportLab 경로는 유지합니다.
DOCX는 기존 `summary.docx`와 `sections[]` 경로를 그대로 사용합니다.
다운로드는 저장된 파일을 읽으며 재조회·재생성하지 않습니다. XLSX는 기존 `sections[].tables` 내보내기 범위를 유지합니다.

### 실제 데이터 연결 범위

- 요청형: `report_ondemand_context.py`가 저장된 품목·국가 범위를 합산해 수출액·수입액·무역수지·월평균·국가 수·집중도·국가 순위 변동을 계산합니다. 합산은 SQL의 OR 조건으로 처리하여 같은 품목을 중복 합산하지 않습니다. 금액은 USD로 저장하고 요청형 PDF에서만 억 달러로 표시합니다.
- 요청형 비교는 미리보기에서 확정한 NONE/MOM/YOY/YOY_PERIOD/CUSTOM 기간을 사용합니다. CUSTOM은 기간 합계만 비교하며 월별로 짝짓지 않습니다. 비교 기간이 미검증이면 비교값은 null이며 비교 없음과 구분합니다. 이전 기간에만 존재하는 국가도 기타의 비교 합계에 포함합니다.
- 국가 순위는 수출액이 양수인 국가를 대상으로 하며 기존 결과표와 PDF에 같은 기준을 적용합니다. 기사 출처는 실제 포함된 기사의 SOURCE 코드로 구성하여 문서 메타데이터에도 반영합니다.
- 요청형 SUMMARY는 항상 표시하며 KPI/TOP_COUNTRIES/LOCAL_ISSUES는 선택 시에만 표시합니다. GROWTH_RISK는 최소 규모 기준 확정 전까지 PDF에서 숨깁니다. 요약·검토 문장과 문장 검증 결과는 임의 생성하지 않습니다. 요청 경로는 CHAT/FORM만 사용하며 질문 원문은 템플릿에 전달하지 않습니다. 요청 지표는 표시만 하고 계산·정렬은 수출액을 유지합니다.
- 월간: 품목별 수출액, 전월/전년 대비, 13개월 추이와 1년 전 비교, 국가별 추이·순위·비중.
- 분기: 3개월 합계, 전분기/전년 동분기 대비, 8개 분기·3개월 추이, 국가별 추이·순위.
- 국가 조건은 기존 문서와 같게 적용합니다. 선택 국가가 있으면 그 국가 범위 안의 금액·순위·비중입니다.
  관심 품목이 없고 국가만 선택한 구독은 `전체 품목` 한 항목으로 집계합니다. `TOTAL` 국가 행은 합산하지 않습니다.
- 과거 월이 적재·검증되지 않았다면 `null`, 검증된 월에서 해당 거래 행이 없다면 `0`입니다.
  일부 월이 미준비인 분기도 `null`입니다. 샘플 JSON은 실제 발행에 사용하지 않습니다.
- 주간 기사 수는 표시 카드 수(최대 20건)와 별도로 전체 관심 범위를 집계합니다.
  일반 기사는 단신속보를 제외한 기사이며 한국경제·파이낸셜뉴스도 실제 출처명으로 표시합니다.
  국가별 표는 다국가 기사를 각 국가에 포함하고 국가 미분류 기사는 제외하므로 전체 기사 수와 다를 수 있습니다.
- 월간·분기 기사 카드는 기존 문서의 최신 최대 5건, 주간 단신 목록은 최신 최대 5건입니다.
- AI 요약/원인 분석, 주요 이슈 선정, 급증 키워드·8주 비교, 문장 수치 검증, 일회성 판정은 이번 연결에 포함하지 않습니다.
  미연결 안내 또는 `null`로 표시하며 임의의 수치나 검증 통과 표시를 만들지 않습니다.

### 워커 환경 설치

API와 워커가 사용하는 같은 Python 환경에서 실행합니다. Chromium은 실제 워커 실행 계정으로 설치합니다.

```powershell
python -m pip install -r backend/requirements-api.txt
python -m playwright install chromium
```

Linux 배포 환경은 `python -m playwright install --with-deps chromium`으로 OS 의존성도 설치합니다.
`API_REPORT_FONT`에 워커에서 읽을 수 있는 한글 TTF 경로를 설정합니다. Windows 기본값은
`C:/Windows/Fonts/malgun.ttf`입니다. 폰트는 HTML 안에 삽입하며 외부 자원 요청과 JavaScript 실행은 차단합니다.
폰트·라이브러리·Chromium이 없으면 명시적 오류가 나며 기존 PDF 디자인으로 몰래 대체하지 않습니다.
설치와 코드 반영 후 워커를 재시작해야 합니다. DB 마이그레이션, Redis 변경, 프론트 수정은 필요하지 않습니다.

### 검증

```powershell
python -m unittest backend.tests.test_report_templates backend.tests.test_periodic_report_pdf backend.tests.test_report_pagination backend.tests.test_ondemand_report_pdf
# 실제 Chromium으로 정기 3종·요청형 생성 → 저장 → API 다운로드 → 재다운로드 검증 (SQLite/fakeredis)
$env:RUN_REPORT_PDF_TESTS = '1'
python -m unittest backend.tests.test_periodic_report_pdf backend.tests.test_report_pagination backend.tests.test_ondemand_report_pdf
```

`REPORT_PDF_QA_DIRECTORY`를 지정하면 위 통합 테스트의 가상 검증 데이터 PDF/HTML을 해당 폴더에 남깁니다.
일반 테스트에서는 Chromium 실행 통합 테스트를 건너뛰며, 위 환경 변수를 설정한 검증이 별도로 필요합니다.

목차·보고서 정보는 내용 높이에 따라 여러 쪽으로 나뉩니다. 긴 품목·국가 목록은 내용을 생략하지 않고
`(계속)` 행으로 이어집니다. 본문 시작 쪽, 목차의 대상 쪽, 전체 페이지 수는 늘어난 앞부분 쪽 수를
반영해 함께 계산합니다. 기본 글꼴·색상·표·여백은 그대로 사용합니다.
`test_report_pagination`은 네 종류의 품목 1·10·30개 및 긴 정보 100개 조건에서 인쇄 영역과 목차 대상을
확인합니다. `REPORT_PAGINATION_QA_DIRECTORY`를 지정하면 월간·요청형 10·30개 검증 PDF/HTML을 남깁니다.
