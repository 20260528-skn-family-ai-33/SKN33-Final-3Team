"""팀 사전 → 태그 INSERT → 국가별 집계. 합성 자료·임시 SQLite만 사용합니다."""
import copy
import hashlib
import json
import re
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import load_project
from news.mysql_store import MySQLArticleStore
from news.project_tags import read_project_dictionary, tag_for_load
from .helpers import prepared
from .test_daily_scheduler import local, fixtures, offline_runner, job, NOW, DAY, ROOT
from .test_loader_views import setup
from .test_admin_erd import admin_db


def strict_tags(db):
    with db.begin() as conn:
        conn.execute(text('DROP TABLE article_tag'))
        conn.execute(text('''CREATE TABLE article_tag (
            tag_id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL REFERENCES article(article_id),
            country_code TEXT REFERENCES country(country_code), hs_code TEXT, hs_ver TEXT, keyword_text TEXT,
            FOREIGN KEY (hs_code,hs_ver) REFERENCES hs_code(hs_code,hs_ver),
            UNIQUE(article_id,country_code), UNIQUE(article_id,hs_code,hs_ver), UNIQUE(article_id,keyword_text),
            CHECK ((country_code IS NOT NULL)+(hs_code IS NOT NULL)+(keyword_text IS NOT NULL)=1),
            CHECK ((hs_code IS NULL)=(hs_ver IS NULL)))'''))


def test_daily_generates_tags_stores_and_counts_countries(local):
    fixtures(local)
    strict_tags(local.db)
    path = local.tmp / 'responses.json'
    spec = json.loads(path.read_text())
    for url, response in spec.items():
        if '/article/' in url or '/news/2026' in url:
            response['body'] = response['body'].replace('100억원 계약을 체결했다.', '100억원 계약을 체결했다. HS 854200 품목이다.')
    path.write_text(json.dumps(spec))
    calls = []
    result = job.run(local.root, now=NOW, env_path=local.env, runner=offline_runner(local, calls, bounded=True))
    assert result['status'] == 'complete' and result['mysql']['inserted'] == 2
    assert result['mysql']['include_tags'] and result['mysql']['generate_tags']
    assert '--include-tags' in calls[-1] and '--generate-tags' in calls[-1]
    generation = result['mysql']['tag_generation']
    assert generation['countries_loaded'] == 2 and generation['products_loaded'] == 1
    detail = json.loads((local.root / result['mysql']['result_file']).read_text())
    assert all(r['generated_tagging']['dictionary_sha256'] == generation['dictionary_sha256'] for r in detail['article_results'])
    with local.db.begin() as conn:
        view = re.search(r'CREATE VIEW v_article_source_country_day.*?;', (ROOT / 'db/mysql/init/01_schema.sql').read_text()).group()
        conn.execute(text(view))
        counts = dict(conn.execute(text('SELECT country_code,SUM(article_count) FROM v_article_source_country_day GROUP BY country_code')).all())
        assert counts == {'KR': 2, 'US': 2}
        assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar() == 2
        assert conn.execute(text('SELECT COUNT(*) FROM article_tag WHERE hs_code IS NOT NULL')).scalar() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM article_tag WHERE keyword_text='수출'")).scalar() == 2
        before_tags = conn.execute(text('SELECT COUNT(*) FROM article_tag')).scalar()
        by_source = conn.execute(text('''SELECT a.source_code,t.country_code,COUNT(DISTINCT a.article_id)
            FROM article a JOIN article_tag t ON t.article_id=a.article_id WHERE t.country_code IS NOT NULL
            GROUP BY a.source_code,t.country_code ORDER BY a.source_code,t.country_code''')).all()
        assert by_source == [('FNNEWS','KR',1),('FNNEWS','US',1),('HANKYUNG','KR',1),('HANKYUNG','US',1)]
    repeat = job.run(local.root, now=NOW, env_path=local.env, runner=offline_runner(local, [], bounded=True))
    assert repeat['status'] == 'complete' and repeat['mysql']['inserted'] == 0 and repeat['mysql']['existing'] == 2
    with local.db.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM article_tag')).scalar() == before_tags
        assert dict(conn.execute(text('SELECT country_code,SUM(article_count) FROM v_article_source_country_day GROUP BY country_code')).all()) == counts
    assert not (local.tmp / 'network_attempts.log').exists()


def test_dictionary_transaction_aliases_and_unambiguous_hs(local):
    with local.db.begin() as conn:
        conn.execute(text("INSERT INTO country VALUES ('ZZ','전세계','TOTAL')"))
        conn.execute(text("INSERT INTO hs_code VALUES ('85','HS2022','상위분류'),('854200','HS2017','이전 판본')"))
    with local.db.connect() as conn:
        dictionary = read_project_dictionary(conn)
        assert not conn.in_transaction()
        original = prepared()
        original['body_masked'] = 'USA로 수출한다. HS 854200 품목이다. 전세계 시장을 살핀다.'
        before = copy.deepcopy(original)
        tagged = tag_for_load(original, dictionary)
        assert original == before
        assert all(tagged[k] == original[k] for k in original if k != 'tagging')
        tags = tagged['tagging']
        assert [t['country_code'] for t in tags['tags'] if t['kind']=='country'] == ['US']
        assert [(t['hs_code'],t['hs_ver']) for t in tags['tags'] if t['kind']=='hs'] == [('854200','HS2022')]
        assert not tags['unresolved']
        assert all(len(p['code']) != 2 for p in dictionary['products'])


def test_generated_tags_add_to_existing_without_rewriting_local_data(local):
    args = setup(local.tmp); args.apply = True
    assert load_project.execute(args, lambda: local.db)['inserted'] == 2
    paths = [local.root/'articles.sqlite3', local.root/'prepared.sqlite3', *sorted((local.root/'articles').glob('*.json'))]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    args.include_tags = args.generate_tags = True
    result = load_project.execute(args, lambda: local.db)
    assert result['status'] == 'complete' and result['existing'] == 2 and result['inserted'] == 0
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    with local.db.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM article_tag WHERE country_code='US'")).scalar() == 2


def test_plan_does_not_read_database(local):
    args = setup(local.tmp); args.include_tags = args.generate_tags = True
    never = Mock(side_effect=AssertionError('DB connection forbidden'))
    result = load_project.execute(args, never)
    assert result['status'] == 'plan_only' and 'tag_generation' not in result
    never.assert_not_called()


def test_missing_dictionary_fails_before_article_insert(local):
    args = setup(local.tmp); args.apply = args.include_tags = args.generate_tags = True
    with local.db.begin() as conn:
        conn.execute(text('DROP TABLE country'))
    result = load_project.execute(args, lambda: local.db)
    assert result['status'] == 'failed' and result['unprocessed'] == 2
    with local.db.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar() == 0


def test_invalid_foreign_key_rolls_back_article_and_tags(local):
    strict_tags(local.db)
    with local.db.connect() as conn:
        dictionary = read_project_dictionary(conn)
        article = tag_for_load(prepared(), dictionary)
        article['tagging']['tags'].append({'kind':'country','country_code':'NOT_IN_DB'})
        with pytest.raises(IntegrityError):
            MySQLArticleStore(conn, {'hankyung':'HANKYUNG'}, include_tags=True).save(article)
        assert not conn.in_transaction()
        assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar() == 0
        assert conn.execute(text('SELECT COUNT(*) FROM article_tag')).scalar() == 0


def test_generated_tags_respect_manual_country_and_hs(admin_db, local):
    from backend.app.services.news_schema import reflect
    args = setup(local.tmp); args.apply = args.include_tags = args.generate_tags = True
    assert load_project.execute(args, lambda: admin_db)['status'] == 'complete'
    with admin_db.begin() as conn:
        table = reflect(conn, 'article')
        rows = conn.execute(table.select()).mappings().all()
        for row in rows:
            metadata = dict(row['admin_meta_json']); metadata.update(country_tags_manual=True, hs_tags_manual=True)
            conn.execute(table.update().where(table.c.article_id==row['article_id']).values(admin_meta_json=metadata))
        conn.execute(text("DELETE FROM article_tag WHERE country_code='US'"))
    repeat = load_project.execute(args, lambda: admin_db)
    assert repeat['status'] == 'complete' and repeat['existing'] == 2
    with admin_db.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM article_tag WHERE country_code='US'")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM article_tag WHERE country_code='KR'")).scalar() == 2


# 누락·오탐·정상 언급을 같은 자료로 생성 → 저장 → 집계 → 재실행까지 검증합니다.
@pytest.mark.parametrize('body,expected', [
    ('인도네시아로 반도체를 수출했다.', ['ID']),
    ('피지컬AI 반도체 수출 계약을 체결했다.', []),
    ('반도체 수출대금의 결제 수단을 다양화했다.', []),
    ('인도네시아와 인도에 반도체를 수출했다.', ['ID', 'IN']),
    ('피지에서 반도체 수출 계약을 체결했다.', ['FJ']),
    ('수단으로 반도체를 수출했다.', ['SD']),
    ('수단에서 수출대금 결제 수단을 협의했다.', ['SD']),
    ('피지컬AI 수출 계약을 피지에서 체결했다.', ['FJ']),
    ('피지로부터의 설탕 수입이 증가했다.', ['FJ']),
    ('피지산 설탕의 수입 관세를 낮췄다.', ['FJ']),
    ('수단으로부터의 원유 수입이 증가했다.', ['SD']),
    ('수단산 원유의 수입 관세를 낮췄다.', ['SD']),
    ('대수단 수출이 증가했다.', ['SD']),
    ('피지산업이라는 시험 상표로 반도체를 수출했다.', []),
    ('피지산 설탕은 피지컬AI와 관련이 없다.', ['FJ']),
    ('결제 수단으로부터의 전환 비용을 계산했다.', []),
    ('거대수단이라는 시험 상표로 반도체를 수출했다.', []),
    ('기업의 대수단 수출과 수단산 원유 수입이 늘었다.', ['SD']),
    ('피지로부터의 수입과 인도네시아로의 수출이 늘었다.', ['FJ', 'ID']),
    ('피지로부터 설탕을 수입했다.', ['FJ']),
    ('피지에서도 반도체를 수입했다.', ['FJ']),
    ('피지와의 무역 협정을 체결했다.', ['FJ']),
    ('수단에서 결제 수단을 협의했다.', ['SD']),
    ('인도네시아와 인도로 반도체를 수출했다.', ['ID', 'IN']),
    ('미국산 반도체 수입이 증가했다.', ['US']),
    ('피지로부터도 설탕을 수입한다.', ['FJ']),
    ('수단으로부터는 원유를 수입한다.', ['SD']),
    ('피지에서부터는 수출 운송비가 증가한다.', ['FJ']),
    ('일본에 수출할 물품의 인도를 마쳤다.', ['JP']),
    ('미국은 관세를 협상 수단으로 활용했다.', ['US']),
    ('미국은 관세를 압박 수단으로 활용했다.', ['US']),
    ('원자재 수입 가격은 톤당 오만원이다.', []),
    ('오만으로 자동차를 수출했다.', ['OM']),
    ('인도에서 반도체를 수입했다.', ['IN']),
    ('피지에서부터도 설탕을 수입한다.', ['FJ']),
    ('피지에서만 수입하던 설탕을 수단에서도 수입한다.', ['FJ', 'SD']),
    ('수단으로부터만 원유를 수입한다.', ['SD']),
    ('피지만으로는 수입 수요를 충족할 수 없다.', ['FJ']),
    ('피지산은 관세가 낮고 수단산도 수입한다.', ['FJ', 'SD']),
    ('수단행 화물과 피지발 설탕이 운송된다.', ['FJ', 'SD']),
    ('피지컬AI와 결제 수단의 개선으로 수출을 확대한다.', []),
    ('수단 정부가 관세를 협상 수단으로 활용했다.', ['SD']),
    ('미국이 관세를 협상 수단으로 삼았다.', ['US']),
    ('미국은 관세를 새로운 수단으로 활용했다.', ['US']),
    ('수단과 미국은 관세 협상을 시작했다.', ['SD', 'US']),
    ('인도로 수출할 물품의 인도를 마쳤다.', ['IN']),
    ('인도에서 선박 인도를 완료했다.', ['IN']),
    ('상품 인도 후 수출대금을 지급한다.', []),
    ('상품을 인도에 수출했다.', ['IN']),
    ('선박을 인도로 수출했다.', ['IN']),
    ('일본에 수출할 선박을 인도했다.', ['JP']),
    ('원자재 수입 가격은 오만 원이다.', []),
    ('원자재 수입 가격은 오만 달러다.', []),
    ('오만 원유의 수입이 증가했다.', ['OM']),
    ('오만산 원유를 수입했다.', ['OM']),
    ('오만의 원유 수출액은 오만 달러다.', ['OM']),
    ('오만 정부가 수입 관세를 조정했다.', ['OM']),
    ('인도 정부가 수입 관세를 조정했다.', ['IN']),
    ('수단은 결제 수단을 변경하고 인도는 물품 인도를 마쳤다.', ['IN', 'SD']),
])
def test_country_context_generation_storage_and_repeat(local, body, expected):
    # 음성 사례뿐 아니라 실제 국가 언급·조사·같은 문장 속 두 용례도 보존합니다.
    with local.db.begin() as conn:
        conn.execute(text("INSERT INTO country VALUES ('IN','인도','COUNTRY'),('ID','인도네시아','COUNTRY'),('FJ','피지','COUNTRY'),('SD','수단','COUNTRY'),('OM','오만','COUNTRY'),('JP','일본','COUNTRY')"))
    strict_tags(local.db)
    original = prepared()
    original.update(title='반도체 수출 계약', body_masked=body)
    before = copy.deepcopy(original)
    with local.db.connect() as conn:
        dictionary = read_project_dictionary(conn)
        tagged = tag_for_load(original, dictionary)
        assert original == before
        countries = sorted(t['country_code'] for t in tagged['tagging']['tags'] if t['kind']=='country')
        assert countries == expected
        loader = MySQLArticleStore(conn, {'hankyung':'HANKYUNG'}, include_tags=True)
        assert loader.save(tagged, return_result=True)['status'] == 'inserted'
        assert loader.save(tagged, return_result=True)['status'] == 'existing'
        counts = conn.execute(text("""SELECT t.country_code,COUNT(DISTINCT a.article_id)
            FROM article a JOIN article_tag t ON t.article_id=a.article_id
            WHERE t.country_code IS NOT NULL GROUP BY t.country_code ORDER BY t.country_code""")).all()
        assert counts == [(code, 1) for code in expected]
        assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar() == 1


def test_long_country_name_without_dictionary_row_does_not_become_short_country(local):
    # DB에 긴 국가명이 없더라도 공통 지명 사전의 긴 이름을 짧은 국가명으로 바꾸지 않습니다.
    with local.db.begin() as conn:
        conn.execute(text("INSERT INTO country VALUES ('IN','인도','COUNTRY')"))
    with local.db.connect() as conn:
        dictionary = read_project_dictionary(conn)
    article = prepared()
    article.update(title='반도체 수출 계약', body_masked='인도네시아에 반도체를 수출했다.')
    assert not [t for t in tag_for_load(article, dictionary)['tagging']['tags'] if t['kind']=='country']
