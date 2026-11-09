# Report Agent 평가 정답 검토표

질문 24개 · 고정 2026-11-09T07:48:25.162107+00:00 · 사람 검토 대기

`INHERIT`는 질문에 없는 조건을 비워 이전 대화 조건을 이어받아야 한다는 뜻이다. null은 채점하지 않는다.

| ID | 유형 | 질문 | 기대 의도 | 연결 | 수출입 | 국가 | 품목 | 기간 | 검토 |
|---|---|---|---|---|---|---|---|---|---|
| H01 | condition_report | 호주 와인 수출 현황을 리포트로 받아볼 수 있을까? | REPORT | [] | EXPORT | ['호주'] | ['와인'] | None | ☐ |
| H02 | condition_report | 싱가포르 대상 화장품 수출 분석 보고서 하나 뽑아 줘 | REPORT | [] | EXPORT | ['싱가포르'] | ['화장품'] | None | ☐ |
| H03 | condition_report | 2024년 하반기 멕시코 자동차 부품 수출 리포트 작성 부탁드립니다 | REPORT | [] | EXPORT | ['멕시코'] | ['자동차 부품'] | {'start': '2024-07', 'end': '2024-12'} | ☐ |
| H04 | condition_report | 최근 1년 대만 반도체 수출 리포트 PDF로 만들어 주세요 | REPORT | [] | EXPORT | ['대만'] | ['반도체'] | {'recent_months': 12} | ☐ |
| H05 | condition_report | 인도랑 브라질 두 나라 의약품 수출 리포트 생성해줘 | REPORT | [] | EXPORT | ['인도', '브라질'] | ['의약품'] | None | ☐ |
| H06 | result_report | 필리핀 라면 수출액 먼저 확인하고 그 데이터로 리포트 만들어줘 | STATISTICS→REPORT | [1] | EXPORT | ['필리핀'] | ['라면'] | None | ☐ |
| H07 | result_report | 의약품 수출 많이 하는 나라 순위 뽑고 그걸 바탕으로 보고서 써줘 | RANKING→REPORT | [1] | EXPORT | None | ['의약품'] | None | ☐ |
| H08 | result_report | 영국 위스키 최근 12개월 수출 추이를 조회해서 결과를 리포트로 남겨줘 | STATISTICS→REPORT | [1] | EXPORT | ['영국'] | ['위스키'] | {'recent_months': 12} | ☐ |
| H09 | result_report | 라면 유망 수출국을 예측한 다음 그 예측 결과로 리포트 만들어 줘 | PROMISING_MARKET→REPORT | [1] | EXPORT | None | ['라면'] | None | ☐ |
| H10 | import_report | 호주산 소고기 수입 동향 보고서 작성해줘 | REPORT | [] | IMPORT | ['호주'] | ['소고기'] | None | ☐ |
| H11 | import_report | 칠레 와인 수입 리포트 하나 만들어 줄래? | REPORT | [] | IMPORT | ['칠레'] | ['와인'] | None | ☐ |
| H12 | import_report | 독일에서 수입하는 자동차 최근 6개월 리포트로 정리해줘 | REPORT | [] | IMPORT | ['독일'] | ['자동차'] | {'recent_months': 6} | ☐ |
| H13 | not_report | 리포트 구독하면 언제 받아볼 수 있어? | CONVERSATION 또는 OUT_OF_SCOPE | — | — | — | — | — | ☐ |
| H14 | not_report | PDF랑 DOCX 중에 뭐가 나아? | CONVERSATION 또는 OUT_OF_SCOPE | — | — | — | — | — | ☐ |
| H15 | not_report | 필리핀 라면 수출 상위 품목 순위만 보여줘, 보고서는 필요 없어 | RANKING 또는 STATISTICS | — | — | — | — | — | ☐ |
| H16 | not_report | FTA가 뭐야? | CONVERSATION | — | — | — | — | — | ☐ |
| H17 | not_report | 대만 반도체 수출이 줄어든 이유 알려줘 | CAUSE 또는 STATISTICS→CAUSE | — | — | — | — | — | ☐ |
| H18 | mixed_request | 영국 위스키 수출 리포트 만들고 위스키 HS코드도 알려줘 | REPORT→HS_LOOKUP | [] | EXPORT | ['영국'] | ['위스키'] | None | ☐ |
| H19 | mixed_request | 멕시코 화장품 수출액 알려 주고, 별도로 칠레 화장품 수출 리포트도 부탁해 | STATISTICS→REPORT | [] | EXPORT | ['칠레'] | ['화장품'] | None | ☐ |
| H20 | mixed_request | 인도 의약품 수출 리포트 만들어 주고 인도 관련 최근 기사도 같이 보여줘 | REPORT→LOCAL_ISSUES | [] | EXPORT | ['인도'] | ['의약품'] | None | ☐ |
| H21 | follow_up | 지금 조건 그대로 리포트로 받아볼게 | REPORT | [] | EXPORT | INHERIT | INHERIT | INHERIT | ☐ |
| H22 | follow_up | 국가만 일본으로 바꿔서 리포트 만들어줘 | REPORT | [] | EXPORT | ['일본'] | INHERIT | INHERIT | ☐ |
| H23 | follow_up | 2024년 전체 기간으로 다시 리포트 뽑아줘 | REPORT | [] | EXPORT | INHERIT | INHERIT | {'start': '2024-01', 'end': '2024-12'} | ☐ |
| H24 | follow_up | 같은 품목과 국가로 수입 리포트도 만들어 줘 | REPORT | [] | IMPORT | INHERIT | INHERIT | INHERIT | ☐ |
