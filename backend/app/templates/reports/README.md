# 정기 리포트 HTML 템플릿

`리포트_양식_최신판.pdf`의 월간·주간·분기 양식을 Jinja2 HTML로 옮긴 템플릿입니다. 수치·문장·기사·목록은 모두 컨텍스트 변수로 받고,
템플릿에는 고정 문구(제목 틀·계산식 설명·면책 문구)만 있습니다. A4 한 쪽이 `<section class="page">` 하나이며, 그래프는 JavaScript 없는
SVG라 HTML → PDF 변환에서도 그대로 나옵니다.

| 파일 | 내용 |
|---|---|
| `monthly.html` | 월간 (`RPT_MONTHLY`): 표지 · 목차 · 이달의 요약 · 관심 품목별 상세(품목 수만큼) · 현지 이슈 · 데이터 기준 |
| `quarterly.html` | 분기 (`RPT_QUARTERLY`): 표지 · 목차 · 분기 요약 · 분기·월별 추이 · 관심 국가 추이(8개 분기 / 3개월) · 주요 수출국 · 현지 이슈 · 데이터 기준 |
| `weekly.html` | 주간 (`RPT_WEEKLY`): 표지 · 목차 · 이번 주 요약 · 관심 품목·국가 기사 · 급증 키워드·기사 분포 (수출 통계 없음) |
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
- 꺾은선 끝의 마지막 값 라벨은 그래프 영역 안에서 겹치지 않게 펼칩니다. 국가가 많아 자리가 없으면(월간 9개·분기 20개 초과)
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

세 종류 공통:

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

## 기존 리포트 생성과의 관계

현재 `api/reports.py`의 `build_document`가 만드는 문서(`title`, `sections[]`, `meta`)는 요청형 중심이라 위 컨텍스트의 일부만 있습니다.
PDF 생성(`api/files.py`의 `report_pdf`, reportlab)은 이 템플릿을 쓰지 않습니다. 템플릿 기반 PDF로 바꾸는 작업과
전월 대비·품목별 상세·관심 국가 추이·급증 키워드 같은 추가 데이터 생성은 담당자와 협의 후 별도로 진행합니다.
