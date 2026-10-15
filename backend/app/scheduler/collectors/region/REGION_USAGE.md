# 대륙·하위 지역 (market_region, country.region_id)

UN M49 기준으로 대륙 5개·하위 지역 22개를 `market_region`에 넣고, `country.region_id`를 하위 지역으로 채운다.
`region_id`는 M49 숫자 코드다 (예: 아시아 142, 동남아시아 35).

## 실행 (저장소 루트에서)

```bash
python -m backend.app.scheduler.jobs.country_region_load
```

- DB 접속은 루트 `.env`의 `MYSQL_*` 값을 쓴다. 다른 DB는 `--db-url mysql+pymysql://...`
- 여러 번 실행해도 결과가 같다. CSV에 있는 국가만 바꾸고, 매핑이 없는 국가(예: AQ 남극)는 경고로 알려 준다.
- CSV 검증 오류가 있으면 아무것도 적재하지 않는다.

## 기준 파일 (`reference/`)

| 파일 | 내용 |
|---|---|
| `market_region.csv` | `region_id,parent_id,region_level,name_ko` (대륙 1, 하위 지역 2) |
| `country_region.csv` | `country_code,region_name` (국가코드 → 하위 지역 이름) |

분류를 바꾸거나 국가를 추가하면 CSV를 고친 뒤 위 명령을 다시 실행한다.

## 다른 코드에서 쓰기

```python
from backend.app.services.country_region import region_id_of
region_id_of("VN")  # 35, 매핑이 없으면 None
```

### 수출입 적재에서 새 국가를 넣을 때 (TODO, feat/10-table)

`backend/app/services/customs_reference.py`의 `seed_references`는 새 국가를 `region_id` 없이 INSERT한다.
대륙도 함께 들어가게 하려면 그 INSERT를 아래처럼 바꾼다.
서브쿼리는 `market_region`이 아직 적재되지 않은 DB에서도 FK 오류 없이 NULL로 넣기 위한 것이다.

```python
from backend.app.services.country_region import region_id_of

cursor.execute("""INSERT INTO country (country_code,name_ko,country_role,region_id)
    VALUES (%s,%s,'COUNTRY',(SELECT region_id FROM market_region WHERE region_id=%s))""",
    (code, name, region_id_of(code)))
```
