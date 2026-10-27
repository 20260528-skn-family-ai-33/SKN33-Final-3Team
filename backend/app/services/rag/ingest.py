"""기존 저장 기사와 검증된 태그를 읽는 오프라인 색인 입력 어댑터."""
import csv
from pathlib import Path

from sqlalchemy import select, inspect, Table, MetaData

from backend.app.models.news import Article, ArticleTag, COUNTRY
from backend.app.models.hs_code import HsCodeMst
from backend.app.schemas.rag import ArticleSource
from backend.app.services.rag.documents import ArticleDocument
from backend.app.services.rag.vocabulary import Vocabulary
from backend.app.services import news_load as market


def read_articles(engine, sources, *, vocabulary=None, supplements=None):
    """supplements는 저장 본문과 일치하는 정제 전 키워드·본문 상태를 전달합니다."""
    sources = tuple(ArticleSource(s).value for s in sources)
    supplements = supplements or {}
    with engine.connect() as conn:
        hs = {(r.hs_code, r.hs_ver): r.name_ko for r in conn.execute(
            select(HsCodeMst.hs_code, HsCodeMst.hs_ver, HsCodeMst.name_ko))}
        valid_countries = set(conn.execute(select(COUNTRY.c.country_code)).scalars())
        article = Table('article', MetaData(), autoload_with=conn)
        query = select(article).where(article.c.source_code.in_(sources))
        if 'visibility_status' in article.c:
            query = query.where(article.c.visibility_status == 'VISIBLE')
        rows = [dict(r) for r in conn.execute(query).mappings()]
        tags = list(conn.execute(select(ArticleTag.__table__).join(Article, Article.article_id == ArticleTag.article_id)
                                .where(Article.source_code.in_(sources))
                                .order_by(ArticleTag.article_id, ArticleTag.tag_id)).mappings())
    vocabulary = vocabulary or Vocabulary.load()
    vocabulary.products = {ver + ':' + code: [name] for (code, ver), name in hs.items() if len(code) in (4, 6)}
    from backend.app.services.news_keywords.keyword_clean import load_geo
    vocabulary.countries = {row['code']: row['canonical'] for row in load_geo()[0].values()
                            if row['type'] == '국가' and row['code'] in valid_countries}
    grouped = {}
    for tag in tags:
        grouped.setdefault(tag['article_id'], []).append(tag)
    documents = []
    for row in rows:
        country, products, keywords = set(), set(), []
        for tag in grouped.get(row['article_id'], []):
            if tag['country_code'] in valid_countries:
                country.add(tag['country_code'])
            code, ver = tag['hs_code'], tag['hs_ver']
            if row['source_code'] != 'KOTRA_SHORT_NEWS' and code and (code, ver) in hs:
                for n in (4, 6):
                    if len(code) >= n and (code[:n], ver) in hs:
                        products.add((code[:n], ver))
            if tag['keyword_text']:
                keywords.append(tag['keyword_text'])
        candidates = supplements.get((row['source_code'], row['external_id']), [])
        if isinstance(candidates, dict):
            candidates = [candidates]
        missing, provenance = [], []
        extra = next((item for item in candidates if item.get('body_masked') == row['body_masked']), {})
        if candidates and not extra:
            missing.append('stale_keyword_supplement')
        # A current CSV with no full body must not erase a previously verified
        # summary of exactly the same stored text. Keep current keyword evidence.
        if (row['source_code'] == 'KOTRA_MARKET' and extra.get('body_available') is False
                and not extra.get('summary_fallback') and any(
                    item.get('body_masked') == row['body_masked'] and item.get('summary_fallback') is True
                    for item in candidates)):
            extra = {**extra, 'summary_fallback': True}
        original_keywords = extra.get('keywords')
        if row['source_code'] in ('HANKYUNG', 'FNNEWS'):
            original_keywords = keywords
        # 현재 기사 태그가 최종 주제어의 기준입니다. 이전 원문 키워드는 구체어 복원에만 사용합니다.
        cleaned = vocabulary.clean(keywords)
        if original_keywords is not None:
            original = vocabulary.clean(original_keywords)
            selected = set(cleaned['kw_topic'])
            subterms = [term for term in original['kw_sub']
                        if selected & set(vocabulary.clean([term])['kw_topic'])]
            cleaned['kw_sub'] = list(dict.fromkeys([*cleaned['kw_sub'], *subterms]))
        if row['source_code'] in ('KOTRA_SHORT_NEWS', 'HANKYUNG', 'FNNEWS'):
            # DB에서 확정한 대표어를 로컬 파일 사전이 다시 바꾸지 않습니다.
            cleaned['kw_topic'] = list(dict.fromkeys(keywords))
        if row['source_code'] == 'KOTRA_SHORT_NEWS':
            subterms = vocabulary.body_subterms(row['body_masked'] or '', cleaned['kw_topic'])
            if subterms:
                recovered = vocabulary.clean(subterms)
                cleaned['kw_sub'] = list(dict.fromkeys([*cleaned['kw_sub'], *recovered['kw_sub']]))
                provenance.append('kw_sub:dictionary_body_match')
        if original_keywords is None:
            missing.append('original_kw_sub')
        body = row['body_masked'] or ''
        if row['source_code'] == 'KOTRA_MARKET' and 'body_available' not in extra:
            # 기존 적재 함수는 본문 누락 시 요약/제목을 저장하므로 길이로 추정하지 않습니다.
            missing.append('body_origin')
        summary_fallback = (row['source_code'] == 'KOTRA_MARKET'
                            and extra.get('summary_fallback') is True
                            and extra.get('body_available') is False and bool(body.strip()))
        if summary_fallback:
            missing.append('body')
            provenance.append('body:summary_fallback')
        elif extra.get('body_available') is False or body.strip() == row['title'].strip():
            body = ''
            missing.append('body')
        if not country:
            missing.append('countries')
        if not products:
            missing.append('products')
        if not row['source_url']:
            missing.append('source_url')
        if row['source_code'] in ('HANKYUNG', 'FNNEWS'):
            provenance.append('country_role:mentioned')
        documents.append(ArticleDocument(article_id=row['article_id'], source_code=row['source_code'],
            external_id=row['external_id'], title=row['title'], published_date=row['published_date'],
            source_url=row['source_url'] or '', body=body, category=row['category'], countries=sorted(country),
            products=[{'hs_code': code, 'hs_ver': ver} for code, ver in sorted(products)],
            kw_topic=cleaned['kw_topic'], kw_sub=cleaned['kw_sub'] if original_keywords is not None or 'kw_sub:dictionary_body_match' in provenance else (),
            raw_keywords=tuple(k for k in original_keywords if k.strip()) if original_keywords is not None else None,
            categories=cleaned['kw_category'], keywords_ready=True,
            metadata_missing=missing, provenance=provenance))
    return documents, vocabulary


def market_supplements(path, *, preserve_candidates=False):
    output = {}
    csv.field_size_limit(2**31-1)
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        for raw in csv.DictReader(stream):
            row = market.article_row(raw)
            body = market.mask(market.html_to_text(raw.get('newsBdt')))
            key = (row['source_code'], row['external_id'])
            value = {'body_masked': row['body_masked'], 'body_available': bool(body),
                     'keywords': [raw.get('kwrd', '')]}
            if preserve_candidates:
                summary = market.mask(market.html_to_text(raw.get('cntntSumar')))
                if not body and summary and row['body_masked'] == summary:
                    value['summary_fallback'] = True
                if 'kwrd' not in raw:
                    value.pop('keywords')  # Unknown original keywords are not known-empty.
                output.setdefault(key, []).append(value)
            else:
                output[key] = value  # Keep the existing scheduler adapter contract.
    return output
