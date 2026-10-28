"""출처별 기사 입력과 Chroma 메타데이터. 본문은 이미 정제·마스킹된 값을 받습니다."""
import hashlib
import json
from typing import Annotated

from pydantic import Field, StrictBool, StrictStr, model_validator

from backend.app.schemas.rag import (
    ArticleKey, CalendarDate, CountryCode, HsProduct, PositiveInt, Text, _unique,
)


class ArticleDocument(ArticleKey):
    article_id: PositiveInt
    title: Text
    published_date: CalendarDate
    source_url: str
    body: StrictStr
    category: str | None = None  # 원천 분류: 키워드 기반 categories와 구분
    countries: tuple[CountryCode, ...] = ()
    products: tuple[HsProduct, ...] = ()
    kw_topic: tuple[Text, ...] = ()
    kw_sub: tuple[Text, ...] = ()
    raw_keywords: tuple[Text, ...] | None = None
    categories: tuple[Text, ...] = ()
    keywords_ready: StrictBool = False
    metadata_missing: tuple[Text, ...] = ()
    provenance: tuple[Text, ...] = ()

    @model_validator(mode='after')
    def check_sets(self):
        for field in ('countries', 'kw_topic', 'kw_sub', 'categories'):
            _unique(getattr(self, field), field)
        _unique(tuple((p.hs_code, p.hs_ver) for p in self.products), 'HS')
        return self

    @property
    def identity(self):
        return json.dumps(self.key, ensure_ascii=False, separators=(',', ':'))

    @property
    def version(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def metadata(self, chunk, raw_hash):
        data = self.model_dump(mode='json', exclude={'body'})
        meta = {'article_key': self.identity, 'source_code': self.source_code.value,
                'external_id': self.external_id, 'article_id': self.article_id,
                'title': self.title, 'source_url': self.source_url,
                'published_date': self.published_date.isoformat(),
                'published_ymd': int(self.published_date.strftime('%Y%m%d')),
                'content_version': self.version, 'chunk': chunk, 'text_hash': raw_hash,
                'record_type': 'body',
                'payload': json.dumps(data, ensure_ascii=False, sort_keys=True)}
        if self.countries:
            meta['countries'] = list(self.countries)
        if self.kw_topic:
            meta['keywords'] = list(self.kw_topic)
        hs_keys = sorted({p.hs_ver + ':' + p.hs_code for p in self.products})
        if hs_keys:
            meta['hs_keys'] = hs_keys
        return meta


def from_metadata(metadata, body):
    article = ArticleDocument.model_validate({**json.loads(metadata['payload']), 'body': body})
    if article.identity != metadata['article_key'] or article.source_code.value != metadata['source_code']:
        raise ValueError('article_identity_mismatch')
    return article
