# Report Agent 평가 정답 검토표

질문 32개 · 고정 2026-11-09T07:43:07.969748+00:00 · 사람 검토 대기

`INHERIT`는 질문에 없는 조건을 비워 이전 대화 조건을 이어받아야 한다는 뜻이다. null은 채점하지 않는다.

| ID | 유형 | 질문 | 기대 의도 | 연결 | 수출입 | 국가 | 품목 | 기간 | 검토 |
|---|---|---|---|---|---|---|---|---|---|
| L01 | condition_report | 미국 화장품 수출 리포트 만들어줘 | REPORT | [] | EXPORT | ['미국'] | ['화장품'] | None | ☐ |
| L02 | condition_report | 베트남 반도체 최근 6개월 수출 보고서 작성해줘 | REPORT | [] | EXPORT | ['베트남'] | ['반도체'] | {'recent_months': 6} | ☐ |
| L03 | condition_report | 2025년 1월부터 3월까지 일본 라면 수출 리포트 PDF로 뽑아줘 | REPORT | [] | EXPORT | ['일본'] | ['라면'] | {'start': '2025-01', 'end': '2025-03'} | ☐ |
| L04 | condition_report | 중국이랑 미국 대상으로 기타 화장품 수출 리포트 만들어 줘 | REPORT | [] | EXPORT | ['중국', '미국'] | ['화장품'] | None | ☐ |
| L05 | condition_report | HS 330499 독일 수출 리포트 생성해줘 | REPORT | [] | EXPORT | ['독일'] | ['330499'] | None | ☐ |
| L06 | condition_report | 태국 의류 최근 3개월 수출을 리포트로 정리해줘 | REPORT | [] | EXPORT | ['태국'] | ['의류'] | {'recent_months': 3} | ☐ |
| L07 | condition_report | 인도네시아 화장품 수출 리포트를 전년 동기 대비로 만들어줘 | REPORT | [] | EXPORT | ['인도네시아'] | ['화장품'] | None | ☐ |
| L08 | condition_report | 캐나다 자동차 부품 수출 동향 보고서 부탁해 | REPORT | [] | EXPORT | ['캐나다'] | ['자동차 부품'] | None | ☐ |
| L09 | result_report | 미국 화장품 최근 3개월 수출액 조회하고 그 결과로 리포트 만들어줘 | STATISTICS→REPORT | [1] | EXPORT | ['미국'] | ['화장품'] | {'recent_months': 3} | ☐ |
| L10 | result_report | 반도체 수출 상위 국가 순위 보여주고 그 결과로 리포트 만들어줘 | RANKING→REPORT | [1] | EXPORT | None | ['반도체'] | None | ☐ |
| L11 | result_report | 베트남 화장품 수출 추이 보여주고 이 결과로 보고서 작성해줘 | STATISTICS→REPORT | [1] | EXPORT | ['베트남'] | ['화장품'] | None | ☐ |
| L12 | result_report | 일본 맥주 최근 6개월 수출 보여주고 그걸 리포트로 저장해줘 | STATISTICS→REPORT | [1] | EXPORT | ['일본'] | ['맥주'] | {'recent_months': 6} | ☐ |
| L13 | result_report | 화장품 수출 유망시장 알려주고 그 결과로 리포트도 만들어줘 | PROMISING_MARKET→REPORT | [1] | EXPORT | None | ['화장품'] | None | ☐ |
| L14 | result_report | 중국 라면 수출액을 2025년 1분기로 조회한 뒤 그 결과를 리포트로 만들어줘 | STATISTICS→REPORT | [1] | EXPORT | ['중국'] | ['라면'] | {'start': '2025-01', 'end': '2025-03'} | ☐ |
| L15 | import_report | 중국산 반도체 수입 리포트 만들어줘 | REPORT | [] | IMPORT | ['중국'] | ['반도체'] | None | ☐ |
| L16 | import_report | 일본에서 들여오는 맥주 수입 보고서 작성해줘 | REPORT | [] | IMPORT | ['일본'] | ['맥주'] | None | ☐ |
| L17 | import_report | 베트남 의류 최근 3개월 수입 리포트 PDF로 만들어줘 | REPORT | [] | IMPORT | ['베트남'] | ['의류'] | {'recent_months': 3} | ☐ |
| L18 | import_report | 미국산 쇠고기 수입액 조회하고 그 결과로 리포트 만들어줘 | STATISTICS→REPORT | [1] | IMPORT | ['미국'] | ['쇠고기'] | None | ☐ |
| L19 | not_report | 리포트는 어디서 볼 수 있어? | CONVERSATION 또는 OUT_OF_SCOPE | — | — | — | — | — | ☐ |
| L20 | not_report | 리포트 만드는 데 보통 얼마나 걸려? | CONVERSATION 또는 OUT_OF_SCOPE | — | — | — | — | — | ☐ |
| L21 | not_report | 보고서 말고 그냥 미국 화장품 최근 3개월 수출액만 알려줘 | STATISTICS | — | — | — | — | — | ☐ |
| L22 | not_report | 베트남 화장품 수출이 왜 늘었어? | CAUSE | — | — | — | — | — | ☐ |
| L23 | not_report | 화장품 HS코드 알려줘 | HS_LOOKUP | — | — | — | — | — | ☐ |
| L24 | not_report | 미국 관세 관련 최근 기사 보여줘 | LOCAL_ISSUES | — | — | — | — | — | ☐ |
| L25 | mixed_request | 미국 화장품 최근 3개월 수출액 알려주고, 그와 별개로 중국 화장품 수출 리포트도 만들어줘 | STATISTICS→REPORT | [] | EXPORT | ['중국'] | ['화장품'] | None | ☐ |
| L26 | mixed_request | 일본 라면 수출 리포트 만들어 주고 라면 수출이 왜 늘었는지도 설명해줘 | REPORT→CAUSE | [] | EXPORT | ['일본'] | ['라면'] | None | ☐ |
| L27 | mixed_request | 베트남 반도체 수출 리포트 만들어 주고 관련 기사도 보여줘 | REPORT→LOCAL_ISSUES | [] | EXPORT | ['베트남'] | ['반도체'] | None | ☐ |
| L28 | mixed_request | 독일 자동차 부품 최근 6개월 수출을 보여주고 결과로 리포트 만든 다음 관련 기사도 찾아줘 | STATISTICS→REPORT→LOCAL_ISSUES | [1] | EXPORT | ['독일'] | ['자동차 부품'] | {'recent_months': 6} | ☐ |
| L29 | follow_up | 이 조건으로 리포트 만들어줘 | REPORT | [] | EXPORT | INHERIT | INHERIT | INHERIT | ☐ |
| L30 | follow_up | 같은 품목으로 중국 대상 리포트도 만들어줘 | REPORT | [] | EXPORT | ['중국'] | INHERIT | INHERIT | ☐ |
| L31 | follow_up | 기간을 최근 6개월로 바꿔서 리포트 만들어줘 | REPORT | [] | EXPORT | INHERIT | INHERIT | {'recent_months': 6} | ☐ |
| L32 | follow_up | 방금 조건 그대로 수입 기준 리포트로 만들어줘 | REPORT | [] | IMPORT | INHERIT | INHERIT | INHERIT | ☐ |
