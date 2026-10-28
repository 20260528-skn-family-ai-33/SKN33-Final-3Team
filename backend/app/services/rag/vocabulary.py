"""공통 정제 사전 재사용. 기사 검색 중 외부 저장소를 조회하지 않습니다."""
from dataclasses import dataclass, field
import re

from backend.app.services.news_keywords import keyword_clean as kc


def contains(term, text):
    if term.isascii():
        return bool(re.search(r'(?<![A-Z0-9])' + re.escape(term) + r'(?![A-Z0-9])', text))
    return term in text


@dataclass
class Vocabulary:
    synonyms: dict = field(default_factory=dict)
    hypernyms: dict = field(default_factory=dict)
    categories: object = field(default_factory=dict)
    products: dict = field(default_factory=dict)
    countries: dict = field(default_factory=dict)

    @classmethod
    def load(cls, *, products=None, countries=None):
        for path in (kc.SYNONYMS_PATH, kc.HYPERNYMS_PATH, kc.CATEGORIES_PATH):
            if not path.is_file():
                raise FileNotFoundError('required_rag_dictionary_missing')
        return cls(kc.load_synonyms(), kc.load_hypernyms(), kc.load_categories(), products or {}, countries or {})

    def clean(self, keywords):
        return kc.process_article(','.join(keywords), '', kc.load_geo(), kc.load_exceptions(),
                                  self.synonyms, self.categories, self.hypernyms)

    def body_subterms(self, body, topics):
        """선택된 최종 주제어의 구체 표현 중 실제 본문에 있는 사전 표현만 복원합니다."""
        normalized = kc.normalize(body)
        found = []
        for term in set(self.synonyms) | set(self.hypernyms):
            canonical = self.synonyms.get(term, term)
            parent = self.hypernyms.get(canonical, canonical)
            if parent in topics and contains(term, normalized):
                found.append(term)
        return sorted(found)

    def product_terms(self, products):
        terms = []
        for p in products:
            key = p.hs_ver + ':' + p.hs_code
            if key not in self.products:
                raise ValueError('confirmed_product_not_in_dictionary')
            terms.extend(self.products[key])
        return tuple(dict.fromkeys(terms))

    def query(self, request):
        terms = [request.query, *self.product_terms(request.conditions.products)]
        terms.extend(self.countries.get(c, c) for c in request.conditions.geography.codes)
        normalized = kc.normalize(' '.join(terms))
        for alias, canonical in self.synonyms.items():
            if alias and (contains(alias, normalized) or contains(canonical, normalized)):
                terms.extend((alias, canonical, self.hypernyms.get(canonical, canonical)))
        for child, parent in self.hypernyms.items():
            if contains(child, normalized) or contains(parent, normalized):
                terms.extend((child, parent))
        return '\n'.join(dict.fromkeys(terms))

    def query_categories(self, request):
        query = kc.normalize(request.query)
        found = set()
        for term in set(self.synonyms.values()) | set(self.hypernyms) | set(self.hypernyms.values()):
            if contains(term, query):
                category = kc.category_of(term, self.categories)
                if category:
                    found.add(category)
        return found
