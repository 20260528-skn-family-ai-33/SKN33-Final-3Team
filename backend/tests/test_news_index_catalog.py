"""카탈로그 CLI→실제 합성 SQL→국가 순서 보존 이관→공통 갱신. 운영 접근 없음."""
import csv
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import event

from backend.tests import test_shortnews_pipeline as pipeline_fixture
from backend.app.db.batch_log import ensure_source, Source
from backend.app.db.news_lock import engine_identity
from backend.app.db.session import session_scope
from backend.app.scheduler.jobs import news_embed, news_index_catalog
from backend.app.scheduler.jobs.shortnews_load import reflect
from backend.app.services.rag import catalog_export as exporter, legacy_migration as migration
from backend.app.services.rag.store import GenerationStore, MODEL, persistent_client, read_all
from backend.app.services.rag.vocabulary import Vocabulary
from backend.app.services.news_keywords import keyword_clean as kc

SOURCES = ['KOTRA_MARKET', 'KOTRA_SHORT_NEWS']
ORDER = ['RU', 'KZ', 'UZ', 'AZ', 'BY']


def encode(texts):
    return [[1.0] + [0.0] * 3071 for _ in texts]


def write_raw(path, records):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def collection_rows(root):
    snapshot = GenerationStore(root).snapshot()
    try:
        data = read_all(snapshot.collection('kotra_article'), ['documents', 'metadatas', 'embeddings'])
        return {key: (doc, meta, list(vector)) for key, doc, meta, vector in zip(
            data['ids'], data['documents'], data['metadatas'], data['embeddings'])}
    finally:
        snapshot.client.close()


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    from chromadb.api.types import DefaultEmbeddingFunction
    def forbid_implicit_embedding(self, input):
        raise AssertionError('implicit model forbidden')
    monkeypatch.setattr(DefaultEmbeddingFunction, '__call__', forbid_implicit_embedding)
    case = pipeline_fixture.PipelineTest()
    case.setUp()
    try:
        assert case.run_pipeline()['status'] == 'SUCCESS'
        geo = {code: {'code': code, 'canonical': code, 'type': '국가'} for code in [*ORDER, 'US']}
        with patch('backend.app.services.news_keywords.keyword_clean.load_geo', return_value=(geo, {})):
            with session_scope(case.db.engine) as session:
                ensure_source(session, Source('KOTRA_MARKET', '합성 시장', {}))
                session.execute(reflect(case.db.engine, 'country').insert(),
                    [{'country_code': code, 'name_ko': '합성 ' + code} for code in ORDER])
                template = {k: v for k, v in case.db.rows('article')[0].items() if k != 'article_id'}
                records = []
                for aid, title, body in [(201, '합성 다국가 기사', '다국가 합성 본문입니다. ' * 130),
                                         (202, '합성 대조 기사', '순서가 그대로인 대조 본문입니다.')]:
                    body = body.strip()  # Match the real HTML/text cleanup used before DB loading.
                    row = template | {'article_id': aid, 'source_code': 'KOTRA_MARKET',
                        'external_id': str(aid), 'title': title, 'body_masked': body}
                    session.execute(reflect(case.db.engine, 'article').insert().values(**row))
                    records.append({'bbstxSn': str(aid), 'newsTitl': title, 'newsBdt': body,
                        'othbcDt': '2026-10-01', 'kotraNewsUrl': 'https://example.org/' + str(aid),
                        'infoCl': '', 'kwrd': 'AI', 'cntntSumar': ''})
                tags = [{'article_id': 201, 'country_code': code, 'keyword_text': None} for code in ORDER]
                tags += [{'article_id': 202, 'country_code': 'US', 'keyword_text': None}]
                tags += [{'article_id': aid, 'country_code': None, 'keyword_text': 'AI'} for aid in (201, 202)]
                session.execute(reflect(case.db.engine, 'article_tag').insert(), tags)
            dictionary = tmp_path / 'dictionaries'
            dictionary.mkdir()
            (dictionary / 'synonyms.csv').write_text('표기,대표어\nAI,AI\n', encoding='utf-8-sig')
            (dictionary / 'hypernyms.csv').write_text('하위어,상위어\n', encoding='utf-8-sig')
            (dictionary / 'topic_categories.csv').write_text('주제어,대분류,중분류\nAI,산업,품목\n', encoding='utf-8-sig')
            raw = tmp_path / 'news_raw_synthetic.csv'
            write_raw(raw, records)
            def vocabulary():
                return Vocabulary(kc.load_synonyms(dictionary / 'synonyms.csv'),
                    kc.load_hypernyms(dictionary / 'hypernyms.csv'), kc.load_categories(dictionary / 'topic_categories.csv'))
            with patch.object(Vocabulary, 'load', side_effect=vocabulary):
                yield {'case': case, 'engine': case.db.engine, 'raw': raw, 'records': records,
                       'dictionary': dictionary, 'catalog': tmp_path / 'catalog.json', 'root': tmp_path}
    finally:
        case.doCleanups()


def export(data, **kwargs):
    options = {'raw_paths': [data['raw']], 'sources': SOURCES, 'dictionary_dir': data['dictionary']}
    options.update(kwargs)
    return exporter.export_catalog(data['engine'], data['catalog'], **options)


def seed_legacy(data, name='legacy'):
    source = data['root'] / name
    client = persistent_client(source)
    col = client.create_collection('kotra_article', embedding_function=None,
        metadata={'embedding_model': MODEL, 'hnsw:space': 'cosine'})
    original = {}
    for row in news_embed.read_rows(data['engine']):
        docs = news_embed.build_docs(row)
        for key, (text, meta) in docs.items():
            original[key] = (text, meta, encode([text])[0])
        col.add(ids=list(docs), documents=[text for text, meta in docs.values()],
            metadatas=[meta for text, meta in docs.values()], embeddings=encode(list(docs)))
    client.close()
    return source, original


def migrate(data, source, root, **kwargs):
    return migration.migrate(source, root, data['catalog'], engine_identity(data['engine']),
        source_stopped=True, batch_size=1, **kwargs)


def test_export_cli_country_order_migration_and_selected_source_refresh(dataset, monkeypatch, capsys):
    from backend.app.db import session
    monkeypatch.setattr(session, 'mysql_url', lambda path: dataset['case'].db.url)
    # Actual CLI argparse + actual SQLite engine + real export/read_articles and CSV dictionaries.
    args = ['--raw', str(dataset['raw']), '--dictionary-dir', str(dataset['dictionary']),
            '--output', str(dataset['catalog']), '--source', 'KOTRA_MARKET', '--source', 'KOTRA_SHORT_NEWS']
    assert news_index_catalog.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'exported' and result['articles'] == 3
    data = json.loads(dataset['catalog'].read_text(encoding='utf-8'))
    assert data['database_id'] == engine_identity(dataset['engine'])
    assert next(r for r in data['articles'] if r['article_id'] == 201)['countries'] == sorted(ORDER)
    source, original = seed_legacy(dataset)
    fingerprint = migration.fingerprint(source)
    catalog_bytes = dataset['catalog'].read_bytes()
    target = dataset['root'] / 'target'
    planned = migrate(dataset, source, target)
    assert planned['status'] == 'planned' and planned['country_order_preserved_articles'] == 1
    assert not target.exists()
    migrated = migrate(dataset, source, target, apply=True)
    assert migrated['status'] == 'ready' and migrated['embedded'] == 0
    copied = collection_rows(target)
    assert set(copied) == set(original)
    for key, (text, old_meta, vector) in original.items():
        saved, meta, saved_vector = copied[key]
        assert saved == text and saved_vector == vector
        assert all(meta[name] == value for name, value in old_meta.items())
        assert meta['text_hash'] == hashlib.sha256(saved.encode()).hexdigest()
    folder = target / 'generations' / migrated['generation']
    persisted = [json.loads(line) for line in (folder / 'articles.jsonl').read_text().splitlines()]
    assert next(r for r in persisted if r['article_id'] == 201)['countries'] == ORDER
    same = migration.fingerprint(target)
    assert migrate(dataset, source, target, apply=True)['status'] == 'already_complete'
    assert migration.fingerprint(target) == same
    provider = Mock(side_effect=encode, usage={})
    short = news_embed.run_sources(['KOTRA_SHORT_NEWS'], db_url=dataset['case'].db.url, root=target, embed=provider)
    assert short['status'] == 'complete' and short['body']['embedded'] == 0, short
    assert collection_rows(target) == copied
    assert provider.call_args_list[0].args[0] == ['AI'] and provider.call_count == 1
    provider.reset_mock()
    market = news_embed.run_sources(['KOTRA_MARKET'], db_url=dataset['case'].db.url, root=target, embed=provider)
    changed = {key for key in original if key.startswith('201:')}
    assert market['status'] == 'complete' and market['body']['embedded'] == len(changed), market
    after = collection_rows(target)
    assert not changed & set(after)
    assert all(after[key] == copied[key] for key in set(copied) - changed)
    assert sum(len(call.args[0]) for call in provider.call_args_list) == len(changed)
    provider.reset_mock()
    repeat = news_embed.run_sources(['KOTRA_MARKET'], db_url=dataset['case'].db.url, root=target, embed=provider)
    assert repeat['status'] == 'complete' and repeat['body']['embedded'] == 0
    provider.assert_not_called()
    assert collection_rows(target) == after
    assert migration.fingerprint(source) == fingerprint
    assert dataset['catalog'].read_bytes() == catalog_bytes


@pytest.mark.parametrize('change', ['title', 'date', 'category', 'body', 'add_country', 'remove_country',
                                    'duplicate_country', 'missing_country_metadata', 'inconsistent_order'])
def test_country_order_fix_does_not_accept_other_legacy_changes(dataset, change):
    export(dataset)
    source, original = seed_legacy(dataset)
    client = persistent_client(source)
    col = client.get_collection('kotra_article', embedding_function=None)
    key = sorted(k for k in original if k.startswith('201:'))[0]
    text, meta, _ = original[key]
    if change == 'title': text = text.replace('합성 다국가 기사', '다른 제목')
    elif change == 'date': text = text.replace('2026-10-01', '2026-10-02')
    elif change == 'category': text = text.replace('] ', ' | 다른분류] ', 1)
    elif change == 'body': text += '다른 본문'
    elif change == 'add_country': meta = meta | {'countries': ORDER + ['US']}
    elif change == 'remove_country': meta = meta | {'countries': ORDER[:-1]}
    elif change == 'duplicate_country': meta = meta | {'countries': ORDER + [ORDER[0]]}
    elif change == 'missing_country_metadata': meta = meta | {'countries': None}
    else:
        meta = meta | {'countries': list(reversed(ORDER))}
        text = text.replace(','.join(ORDER), ','.join(reversed(ORDER)))
    col.update(ids=[key], documents=[text], metadatas=[meta], embeddings=encode([text]))
    client.close()
    before = migration.fingerprint(source)
    target = dataset['root'] / 'bad_target'
    with pytest.raises(ValueError): migrate(dataset, source, target, apply=True)
    assert migration.fingerprint(source) == before
    assert not (target / 'active.json').exists() and not (target / 'database.json').exists()


def test_export_is_select_only_and_preserves_all_db_rows(dataset):
    names = ('article', 'article_tag', 'country', 'batch_run')
    before = {name: dataset['case'].db.rows(name) for name in names}
    statements, connections = [], set()
    def observe(conn, cursor, statement, parameters, context, many):
        statements.append(statement.lstrip().split()[0].upper()); connections.add(id(conn))
    event.listen(dataset['engine'], 'before_cursor_execute', observe)
    try: result = export(dataset)
    finally: event.remove(dataset['engine'], 'before_cursor_execute', observe)
    assert not {'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP', 'REPLACE'} & set(statements)
    assert len(connections) == 1
    assert {name: dataset['case'].db.rows(name) for name in names} == before
    assert result['articles'] == 3


def test_export_preserves_all_candidates_and_selects_matching_body(dataset):
    other = dataset['root'] / 'news_raw_other.csv'
    stale = dataset['records'][0] | {'newsBdt': '과거 다른 본문', 'kwrd': '과거'}
    write_raw(other, [stale, dataset['records'][0], dataset['records'][0]])
    # A stale last row must not overwrite the matching row in the same file either.
    write_raw(dataset['raw'], [*dataset['records'], stale])
    assert exporter.market_supplements(dataset['raw'])[('KOTRA_MARKET', '201')]['body_masked'] == '과거 다른 본문'
    assert len(exporter.market_supplements(dataset['raw'], preserve_candidates=True)[('KOTRA_MARKET', '201')]) == 2
    export(dataset, raw_paths=[other, dataset['raw']])
    data = json.loads(dataset['catalog'].read_text())
    assert len(data['articles']) == 3
    affected = next(row for row in data['articles'] if row['article_id'] == 201)
    assert affected['raw_keywords'] == ['AI'] and 'body_origin' not in affected['metadata_missing']


def test_conflicting_same_body_keywords_fail_without_replacing_catalog(dataset):
    export(dataset);before = dataset['catalog'].read_bytes()
    write_raw(dataset['raw'], [*dataset['records'], dataset['records'][0] | {'kwrd': '충돌'}])
    with pytest.raises(ValueError, match='catalog_conflicting_raw_candidates'): export(dataset)
    assert dataset['catalog'].read_bytes() == before


@pytest.mark.parametrize('change', ['no_raw', 'missing_body', 'wrong_body', 'missing_dictionary', 'no_visibility'])
def test_unverified_export_inputs_fail_without_output(dataset, change):
    kwargs = {}
    if change == 'no_raw': kwargs['raw_paths'] = []
    elif change == 'missing_dictionary': (dataset['dictionary'] / 'synonyms.csv').unlink()
    elif change == 'no_visibility':
        with dataset['engine'].begin() as conn: conn.exec_driver_sql('ALTER TABLE article DROP COLUMN visibility_status')
    else:
        records = dataset['records'][:]
        records[0] = records[0] | {'newsBdt': '' if change != 'wrong_body' else '불일치'}
        write_raw(dataset['raw'], records)
    with pytest.raises(ValueError): export(dataset, **kwargs)
    assert not dataset['catalog'].exists()


def test_unknown_raw_keywords_remain_unknown_and_hidden_articles_are_excluded(dataset):
    records = [{k: v for k, v in row.items() if k != 'kwrd'} for row in dataset['records']]
    write_raw(dataset['raw'], records)
    with dataset['engine'].begin() as conn:
        table = reflect(dataset['engine'], 'article')
        conn.execute(table.update().where(table.c.article_id == 202).values(visibility_status='HIDDEN'))
    export(dataset)
    data = json.loads(dataset['catalog'].read_text())
    assert {r['article_id'] for r in data['articles']} == {1, 201}
    affected = next(r for r in data['articles'] if r['article_id'] == 201)
    assert affected['raw_keywords'] is None and 'original_kw_sub' in affected['metadata_missing']
    assert all(r['visibility_status'] == 'VISIBLE' for r in data['articles'])


@pytest.mark.parametrize('input_kind', ['raw', 'dictionary'])
def test_input_change_during_export_preserves_previous_catalog(dataset, monkeypatch, input_kind):
    export(dataset);before = dataset['catalog'].read_bytes()
    original = exporter.read_articles
    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        changed_path = dataset['raw'] if input_kind == 'raw' else dataset['dictionary'] / 'synonyms.csv'
        with changed_path.open('a') as stream: stream.write('\n')
        return result
    monkeypatch.setattr(exporter, 'read_articles', changing)
    with pytest.raises(ValueError, match='catalog_input_changed_during_export'): export(dataset)
    assert dataset['catalog'].read_bytes() == before


def test_export_rejects_output_input_overlap(dataset):
    before = dataset['raw'].read_bytes()
    with pytest.raises(ValueError, match='catalog_output_overlaps_input'):
        exporter.export_catalog(dataset['engine'], dataset['raw'], raw_paths=[dataset['raw']], dictionary_dir=dataset['dictionary'])
    assert dataset['raw'].read_bytes() == before


def test_cli_failure_does_not_disclose_connection_or_private_input(dataset, monkeypatch, capsys):
    from backend.app.db import session
    sentinel = 'PRIVATE_SYNTHETIC_CONNECTION_VALUE'
    monkeypatch.setattr(session, 'mysql_url', Mock(side_effect=RuntimeError(sentinel)))
    assert news_index_catalog.main(['--output', str(dataset['catalog'])]) == 1
    output = capsys.readouterr().out
    assert sentinel not in output and json.loads(output)['error_code'] == 'catalog_export_failed'
    assert not dataset['catalog'].exists()


def test_export_sql_reads_share_one_snapshot_during_concurrent_update(dataset, monkeypatch):
    with dataset['engine'].connect() as conn:
        assert conn.exec_driver_sql('PRAGMA journal_mode=WAL').scalar().lower() == 'wal'
    original = exporter.read_articles
    def concurrent_update(*args, **kwargs):
        with dataset['engine'].begin() as writer:
            table = reflect(dataset['engine'], 'article')
            writer.execute(table.update().where(table.c.article_id == 201).values(body_masked='새 합성 본문'))
        return original(*args, **kwargs)
    monkeypatch.setattr(exporter, 'read_articles', concurrent_update)
    export(dataset)
    data = json.loads(dataset['catalog'].read_text())
    assert next(row for row in data['articles'] if row['article_id'] == 201)['body'] == dataset['records'][0]['newsBdt']
    assert next(row for row in dataset['case'].db.rows('article') if row['article_id'] == 201)['body_masked'] == '새 합성 본문'


def test_cli_raw_directory_reads_all_matching_files_and_repeat_is_deterministic(dataset, monkeypatch, capsys):
    from backend.app.db import session
    monkeypatch.setattr(session, 'mysql_url', lambda path: dataset['case'].db.url)
    write_raw(dataset['raw'], [dataset['records'][0]])
    write_raw(dataset['root'] / 'news_raw_second.csv', [dataset['records'][1]])
    args = ['--raw-dir', str(dataset['root']), '--dictionary-dir', str(dataset['dictionary']),
            '--output', str(dataset['catalog'])]
    assert news_index_catalog.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['articles'] == 2 and result['raw_files'] == 2
    before = dataset['catalog'].read_bytes()
    assert news_index_catalog.main(args) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'exported'
    assert dataset['catalog'].read_bytes() == before


def test_newspaper_sources_and_db_rows_survive_migration_and_shortnews_refresh(dataset):
    with session_scope(dataset['engine']) as session:
        for source in ('HANKYUNG', 'FNNEWS'):
            ensure_source(session, Source(source, '합성 신문', {}))
    with dataset['engine'].begin() as conn:
        table = reflect(dataset['engine'], 'article')
        template = {k: v for k, v in dataset['case'].db.rows('article')[0].items() if k != 'article_id'}
        # These are SQL fixtures only; no crawler is imported or started.
        for aid, source in [(301, 'HANKYUNG'), (302, 'FNNEWS')]:
            conn.execute(table.insert().values(**(template | {
                'article_id': aid, 'source_code': source, 'external_id': str(aid),
                'title': '합성 신문 ' + str(aid), 'body_masked': '유지해야 하는 합성 신문 본문 ' + str(aid)})))
            conn.execute(reflect(dataset['engine'], 'article_tag').insert(), [
                {'article_id': aid, 'country_code': 'US', 'keyword_text': None},
                {'article_id': aid, 'country_code': None, 'keyword_text': 'AI'}])
    records = [dict(row) for row in dataset['records']]
    records[1]['cntntSumar'], records[1]['newsBdt'] = records[1]['newsBdt'], ''
    write_raw(dataset['raw'], records)
    tags = reflect(dataset['engine'], 'article_tag')
    with dataset['engine'].begin() as conn:
        conn.execute(tags.insert(), {'article_id':201, 'keyword_text':'2027년'})
    db_before = {name: dataset['case'].db.rows(name) for name in ('article', 'article_tag', 'batch_run')}
    export(dataset, sources=[*SOURCES, 'HANKYUNG', 'FNNEWS'])
    source, original = seed_legacy(dataset)
    target = dataset['root'] / 'all_sources'
    migrated = migrate(dataset, source, target, apply=True)
    assert migrated['embedded'] == 0
    before = collection_rows(target)
    provider = Mock(side_effect=encode, usage={})
    short = news_embed.run_sources(['KOTRA_SHORT_NEWS'], db_url=dataset['case'].db.url, root=target, embed=provider)
    assert short['status'] == 'complete' and short['body']['embedded'] == 0
    market = news_embed.run_sources(['KOTRA_MARKET'], db_url=dataset['case'].db.url, root=target, embed=provider)
    assert market['status'] == 'complete' and market['body']['embedded'] == len([key for key in original if key.startswith('201:')])
    after = collection_rows(target)
    protected = {key for key, (_, meta, _) in before.items() if meta['source_code'] in ('HANKYUNG', 'FNNEWS')}
    assert protected == {'301:0', '302:0'}
    assert all(after[key] == before[key] for key in protected)
    assert {name: dataset['case'].db.rows(name) for name in db_before} == db_before


@pytest.mark.parametrize('summary_id', [201, 202])
def test_summary_and_keyword_transition_full_migration_and_refresh(dataset, summary_id):
    from backend.app.scheduler.jobs import rag_index
    from sqlalchemy import delete
    records = [dict(row) for row in dataset['records']]
    summary = next(row for row in records if row['bbstxSn'] == str(summary_id))
    summary['cntntSumar'], summary['newsBdt'] = summary['newsBdt'], ''
    write_raw(dataset['raw'], records)
    tags = reflect(dataset['engine'], 'article_tag')
    with dataset['engine'].begin() as conn:
        conn.execute(tags.insert(), {'article_id':201, 'keyword_text':'2027년'})
        conn.execute(delete(tags).where(tags.c.article_id == 202, tags.c.keyword_text.is_not(None)))
        conn.execute(tags.insert(), {'article_id':202, 'keyword_text':'2027년'})
    db_before = {name: dataset['case'].db.rows(name) for name in ('article','article_tag','batch_run')}
    result = export(dataset)
    assert result['summary_fallback_article_ids'] == [summary_id]
    catalog = json.loads(dataset['catalog'].read_text())
    fallback = next(a for a in catalog['articles'] if a['article_id'] == summary_id)
    assert fallback['body'] == summary['cntntSumar']
    assert 'body' in fallback['metadata_missing'] and 'body_origin' not in fallback['metadata_missing']
    assert 'body:summary_fallback' in fallback['provenance']
    assert next(a for a in catalog['articles'] if a['article_id'] == 201)['kw_topic'] == ['AI']
    assert next(a for a in catalog['articles'] if a['article_id'] == 202)['kw_topic'] == []
    source, original = seed_legacy(dataset)
    before_source = migration.fingerprint(source)
    before_catalog = dataset['catalog'].read_bytes()
    root = dataset['root'] / 'summary_migration'
    plan = migrate(dataset, source, root)
    assert plan['status'] == 'planned' and plan['embedded'] == 0 and not root.exists()
    assert plan['summary_fallback_article_ids'] == [summary_id]
    assert plan['summary_fallback_chunks'] == sum(k.startswith(str(summary_id)+':') for k in original)
    assert plan['keyword_changed_articles'] == 2 and plan['keyword_changed_chunks'] == 3
    assert {x['record_id'] for x in plan['keyword_changes']} == {'201:0','201:1','202:0'}
    assert next(x for x in plan['keyword_changes'] if x['record_id']=='202:0') == {
        'article_id':202,'record_id':'202:0','before':['2027년'],'after':[]}
    applied = migrate(dataset, source, root, apply=True)
    assert applied['status'] == 'ready' and applied['embedded'] == 0
    for key in ('summary_fallback_article_ids','summary_fallback_chunks','keyword_changes'):
        assert applied[key] == plan[key]
    status = json.loads((root/'generations'/applied['generation']/'status.json').read_text())
    assert status['keyword_changes'] == plan['keyword_changes']
    after = collection_rows(root)
    assert set(after) == set(original)
    for key,(text,meta,vector) in after.items():
        assert (text,vector) == (original[key][0], original[key][2])
        assert meta.get('keywords',[]) == json.loads(meta['payload'])['kw_topic']
    assert 'keywords' not in after['202:0'][1]
    target_fingerprint = migration.fingerprint(root)
    repeat = migrate(dataset, source, root, apply=True)
    assert repeat['status'] == 'already_complete' and repeat['keyword_changes'] == plan['keyword_changes']
    assert migration.fingerprint(root) == target_fingerprint
    provider = Mock(side_effect=encode, usage={})
    short = news_embed.run_sources(['KOTRA_SHORT_NEWS'], db_url=dataset['case'].db.url, root=root, embed=provider)
    assert short['status'] == 'complete' and short['body']['embedded'] == 0
    assert collection_rows(root) == after
    market = news_embed.run_sources(['KOTRA_MARKET'], db_url=dataset['case'].db.url, root=root, embed=provider)
    assert market['status'] == 'complete' and market['body']['embedded'] == 2
    stable = collection_rows(root)
    provider.reset_mock()
    # Current raw CSV's body_available=False must not hide persisted summary evidence.
    refreshed = rag_index.refresh(dataset['engine'], ['KOTRA_MARKET'], store=GenerationStore(root),
                                  embed=provider, raw_market=dataset['raw'])
    assert refreshed['embedded'] == 0 and collection_rows(root) == stable
    provider.assert_not_called()
    planned = rag_index.refresh(dataset['engine'], ['KOTRA_MARKET'], store=GenerationStore(root),
                                embed=provider, raw_market=False, dry_run=True)
    assert planned['missing_body'] == 0
    for _,meta,_ in stable.values():
        if meta['article_id'] == summary_id:
            payload = json.loads(meta['payload'])
            assert 'body' in payload['metadata_missing'] and 'body:summary_fallback' in payload['provenance']
    assert {name: dataset['case'].db.rows(name) for name in db_before} == db_before
    assert migration.fingerprint(source) == before_source and dataset['catalog'].read_bytes() == before_catalog


@pytest.mark.parametrize('change', ['full_body', 'changed_without_evidence', 'hidden', 'deleted'])
def test_summary_refresh_follows_current_db_without_restoring_stale_text(dataset, change):
    from backend.app.scheduler.jobs import rag_index
    records = [dict(row) for row in dataset['records']]
    records[1]['cntntSumar'], records[1]['newsBdt'] = records[1]['newsBdt'], ''
    write_raw(dataset['raw'], records)
    export(dataset)
    source, _ = seed_legacy(dataset)
    root = dataset['root']/'summary_transition'
    migrate(dataset, source, root, apply=True)
    provider = Mock(side_effect=encode)
    table = reflect(dataset['engine'], 'article')
    changes = {'visibility_status':'HIDDEN'} if change == 'hidden' else {'body_masked':'새로 확인한 실제 전체 본문'}
    with dataset['engine'].begin() as conn:
        if change == 'deleted':
            tags = reflect(dataset['engine'], 'article_tag')
            conn.execute(tags.delete().where(tags.c.article_id==202))
            conn.execute(table.delete().where(table.c.article_id==202))
        else:
            conn.execute(table.update().where(table.c.article_id==202).values(**changes))
    if change == 'full_body':
        records[1]['newsBdt'] = changes['body_masked']
        write_raw(dataset['raw'],records)
    result = rag_index.refresh(dataset['engine'],['KOTRA_MARKET'],store=GenerationStore(root),
                               embed=provider,raw_market=dataset['raw'])
    assert result['status']=='ready'
    affected = [(text,meta) for text,meta,_ in collection_rows(root).values() if meta['article_id']==202]
    if change in ('hidden', 'deleted'): assert not affected
    elif change == 'changed_without_evidence':
        assert len(affected)==1 and affected[0][1]['record_type']=='missing_body' and affected[0][0]==''
    else:
        assert len(affected)==1 and affected[0][0].endswith(changes['body_masked'])
        payload=json.loads(affected[0][1]['payload'])
        assert 'body' not in payload['metadata_missing'] and 'body:summary_fallback' not in payload['provenance']


@pytest.mark.parametrize('change', ['empty_summary','wrong_summary','title_only','conflicting_full_body'])
def test_unverified_summary_never_overwrites_export(dataset, change):
    export(dataset)
    before=dataset['catalog'].read_bytes()
    records=[dict(row) for row in dataset['records']]
    fallback=records[1]
    fallback['cntntSumar'],fallback['newsBdt']=fallback['newsBdt'],''
    if change=='empty_summary':fallback['cntntSumar']=''
    elif change=='wrong_summary':fallback['cntntSumar']='다른 요약'
    elif change=='title_only':
        fallback['cntntSumar']=''
        table=reflect(dataset['engine'],'article')
        with dataset['engine'].begin() as conn:
            conn.execute(table.update().where(table.c.article_id==202).values(body_masked=fallback['newsTitl']))
    else:records.append(dict(dataset['records'][1]))
    write_raw(dataset['raw'],records)
    with pytest.raises(ValueError):export(dataset)
    assert dataset['catalog'].read_bytes()==before


@pytest.mark.parametrize('before,after', [(['2027년'],[]),(['2027년','AI'],['AI']),(['AI'],['AI','수출']),([],['AI']),(['AI','AI'],['AI'])])
def test_keyword_transition_replaces_derived_field_only(before, after):
    from backend.app.services.rag.documents import ArticleDocument
    doc=ArticleDocument(article_id=5,source_code='KOTRA_MARKET',external_id='synthetic-5',title='합성',
        published_date='2026-10-01',source_url='https://example.org/5',body='합성 본문',kw_topic=after)
    expected=migration.expected_records([doc]);text,_,_=expected['5:0']
    meta={'article_id':5,'chunk':0,'custom_note':'kept'}
    if before:meta['keywords']=before
    changes=[]
    result=migration.validate_row('5:0',text,meta,[1.0,0.0],expected,2,keyword_changes=changes)
    assert result.get('keywords',[])==after and result['custom_note']=='kept'
    assert json.loads(result['payload'])['kw_topic']==after
    assert len(changes)==(before!=after)
    if not after:assert 'keywords' not in result


@pytest.mark.parametrize('change', ['text','id','source','hash','payload','vector','keyword_type'])
def test_keyword_exception_keeps_all_other_validation(change):
    from backend.app.services.rag.documents import ArticleDocument
    doc=ArticleDocument(article_id=5,source_code='KOTRA_MARKET',external_id='synthetic-5',title='합성',
        published_date='2026-10-01',source_url='https://example.org/5',body='합성 본문',kw_topic=['AI'])
    expected=migration.expected_records([doc]);text,_,_=expected['5:0']
    meta={'article_id':5,'chunk':0,'keywords':['2027년']};vector=[1.0,0.0]
    if change=='text':text+='다름'
    elif change=='id':meta['article_id']=6
    elif change=='source':meta['source_code']='FNNEWS'
    elif change=='hash':meta['text_hash']='bad'
    elif change=='payload':meta['payload']='{}'
    elif change=='vector':vector=[float('nan'),0.0]
    else:meta['keywords']='2027년'
    changes=[]
    with pytest.raises(ValueError):migration.validate_row('5:0',text,meta,vector,expected,2,keyword_changes=changes)
    assert changes==[]


@pytest.mark.parametrize('change', ['missing_marker','missing_flag','other_source','unknown_origin','empty_text'])
def test_summary_catalog_requires_consistent_provenance(dataset, change):
    export(dataset)
    data=json.loads(dataset['catalog'].read_text())
    row=next(item for item in data['articles'] if item['article_id']==202)
    row['metadata_missing'].append('body')
    row['provenance'].append('body:summary_fallback')
    if change=='missing_marker':row['provenance'].remove('body:summary_fallback')
    elif change=='missing_flag':row['metadata_missing'].remove('body')
    elif change=='other_source':row['source_code']='FNNEWS'
    elif change=='unknown_origin':row['metadata_missing'].append('body_origin')
    else:row['body']=''
    with pytest.raises(ValueError):migration.validate_catalog(data,engine_identity(dataset['engine']))


def test_summary_transition_report_is_identical_across_batch_sizes(dataset):
    records=[dict(row) for row in dataset['records']]
    records[0]['cntntSumar'],records[0]['newsBdt']=records[0]['newsBdt'],''
    write_raw(dataset['raw'],records)
    tags=reflect(dataset['engine'],'article_tag')
    with dataset['engine'].begin() as conn:
        conn.execute(tags.insert(),{'article_id':201,'keyword_text':'2027년'})
    export(dataset)
    source,original=seed_legacy(dataset)
    before=migration.fingerprint(source)
    small=migrate(dataset,source,dataset['root']/'small')
    large=migration.migrate(source,dataset['root']/'large',dataset['catalog'],engine_identity(dataset['engine']),
                            source_stopped=True,batch_size=1000)
    keys=['migration_id','articles','chunks','embedded','reused','keyword_changes','keyword_changed_articles',
          'keyword_changed_chunks','summary_fallback_article_ids','summary_fallback_chunks']
    assert {k:small[k] for k in keys}=={k:large[k] for k in keys}
    assert small['chunks']==len(original) and migration.fingerprint(source)==before
