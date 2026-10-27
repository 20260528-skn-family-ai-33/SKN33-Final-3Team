"""기존 정제 후 네 출처의 본문 색인을 공통 세대에 반영합니다."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from backend.app.schemas.rag import ALL_SOURCES, ArticleSource
from backend.app.services.rag.ingest import read_articles, market_supplements
from backend.app.services.rag.store import GenerationStore, ROOT, atomic_json, resolve_root, validate_generation_contract
from backend.app.services.rag.vocabulary import Vocabulary


def refresh(engine, sources, *, store=None, embed=None, supplements=None, raw_market=None, dry_run=False):
    sources = tuple(ArticleSource(s) for s in sources)
    if not sources:
        raise ValueError('empty_source_scope')
    store = store or GenerationStore()
    store.require_active_or_no_legacy()
    from backend.app.db.news_lock import engine_identity
    identity = engine_identity(engine)
    if dry_run:
        store.bind_database(identity, dry_run=True)
        return _refresh(engine, sources, store=store, embed=embed, supplements=supplements,
                        raw_market=raw_market, dry_run=True)
    store.root.mkdir(parents=True, exist_ok=True)
    with store.lock:
        store.bind_database(identity)
        return _refresh(engine, sources, store=store, embed=embed, supplements=supplements,
                        raw_market=raw_market, dry_run=False)


def _refresh(engine, sources, *, store, embed, supplements, raw_market, dry_run):
    sources = tuple(ArticleSource(s) for s in sources)
    supplements = {key: list(value) if isinstance(value, list) else [value]
                   for key, value in (supplements or {}).items()}
    previous_supplements = {}
    previous = None
    if (store.root/'active.json').exists():
        if dry_run:
            # 계획은 Chroma를 열지 않습니다. 이전 보조 정보는 읽기 전용 카탈로그만 사용합니다.
            import re
            from types import SimpleNamespace
            generation = json.loads((store.root/'active.json').read_text(encoding='utf-8'))['generation']
            if not re.fullmatch(r'[0-9a-f]{32}', generation):
                raise ValueError('invalid_generation')
            validate_generation_contract(json.loads((store.root/'generations'/generation/'status.json').read_text(encoding='utf-8')))
            previous = SimpleNamespace(generation=generation)
        else:
            previous = store.snapshot()
            previous.client.close()
        from backend.app.services.rag.documents import ArticleDocument
        for line in (store.root/'generations'/previous.generation/'articles.jsonl').read_text(encoding='utf-8').splitlines():
            doc = ArticleDocument.model_validate_json(line)
            if 'body_origin' not in doc.metadata_missing:
                fallback = 'body:summary_fallback' in doc.provenance
                extra = {'body_masked':doc.body, 'body_available':bool(doc.body) and not fallback}
                if fallback:
                    if doc.source_code != ArticleSource.KOTRA_MARKET or 'body' not in doc.metadata_missing or not doc.body.strip():
                        raise ValueError('invalid_summary_fallback_catalog')
                    extra['summary_fallback'] = True
                if doc.raw_keywords is not None and 'original_kw_sub' not in doc.metadata_missing:
                    extra['keywords'] = doc.raw_keywords
                previous_supplements[doc.key] = extra
    if ArticleSource.KOTRA_MARKET in sources:
        if raw_market is None:
            from backend.app.scheduler.jobs.news_load import latest_raw, RAW_DIR
            try:
                raw_market = latest_raw(RAW_DIR)
            except FileNotFoundError:
                raw_market = None
        if raw_market:
            for key, value in market_supplements(raw_market).items():
                supplements.setdefault(key, []).append(value)
    for key, value in previous_supplements.items():
        supplements.setdefault(key, []).append(value)
    vocabulary = Vocabulary.load()
    documents, vocabulary = read_articles(engine, sources, vocabulary=vocabulary, supplements=supplements)
    if dry_run:
        return {'status':'planned', 'articles':len(documents), 'sources':[s.value for s in sources],
                'missing_body':sum(not a.body.strip() or 'body_origin' in a.metadata_missing for a in documents),
                'missing_metadata':sum(bool(a.metadata_missing) for a in documents)}
    owned = embed is None
    if owned:
        from backend.app.scheduler.jobs.news_embed import openai_embed
        embed = openai_embed()
    exported = {'products': vocabulary.products, 'countries': vocabulary.countries,
                'synonyms': vocabulary.synonyms, 'hypernyms': vocabulary.hypernyms,
                'categories': {t: vocabulary.categories.lookup(t) for t in vocabulary.categories.listed}}
    failure = None
    try:
        result = store.publish(documents, sources, embed, vocabulary=exported)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if owned:
            try:
                embed.close()
            except Exception as exc:
                cleanup = {'resource':'embedding','error_type':type(exc).__name__}
                if failure is not None:
                    failure.cleanup_errors = [cleanup]
                else:
                    # active 게시 결과는 그대로 두고 연결 종료 실패를 별도 기록합니다.
                    result.setdefault('cleanup_errors', []).append(cleanup)
    result['selected_articles'] = len(documents)
    result['total_articles'] = result['articles']
    return result


def run(sources=ALL_SOURCES, *, db_url=None, root=None, embed=None, supplements=None, raw_market=None, dry_run=False):
    from backend.app.db.session import make_engine
    engine = None
    started = datetime.now(timezone.utc).isoformat()
    report = {'started_at': started, 'status':'failed', 'sources':[ArticleSource(s).value for s in sources]}
    try:
        engine = make_engine(db_url)
        report['index'] = refresh(engine, sources, store=GenerationStore(root), embed=embed,
                                  supplements=supplements, raw_market=raw_market, dry_run=dry_run)
        report['status'] = 'planned' if dry_run else 'complete'
        if report['index'].get('cleanup_errors'):
            report.update(status='partial', error_code='resource_cleanup_failed',
                          cleanup_errors=report['index']['cleanup_errors'])
    except Exception as exc:
        report['error_code'] = type(exc).__name__
        if getattr(exc, 'cleanup_errors', None):
            report['cleanup_errors'] = exc.cleanup_errors
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception as exc:
                report.setdefault('cleanup_errors', []).append({'resource':'database','error_type':type(exc).__name__})
                if report['status'] in ('complete', 'planned'):
                    report.update(status='partial', error_code='resource_cleanup_failed')
        if not dry_run:
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            try:
                atomic_json(resolve_root(root)/'runs'/(uuid4().hex+'.json'), report)
            except Exception as exc:
                report['report_error'] = type(exc).__name__
                if report['status'] == 'complete':
                    report.update(status='partial', error_code='report_write_failed')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', choices=[s.value for s in ALL_SOURCES])
    parser.add_argument('--root', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    result = run(args.source or ALL_SOURCES, root=args.root, dry_run=args.dry_run)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status'] in ('planned','complete') else 1


if __name__ == '__main__':
    raise SystemExit(main())
