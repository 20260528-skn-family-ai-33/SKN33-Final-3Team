"""기사 RAG 호출 계약. 검색·색인·모델 호출은 수행하지 않습니다."""
from datetime import date, timedelta
from enum import Enum
import re
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import (
    AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field,
    HttpUrl, StrictInt, StringConstraints, model_validator,
)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("공백만 있는 문자열은 사용할 수 없습니다")
    return value


def _calendar_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value)
    raise ValueError("날짜는 YYYY-MM-DD 또는 date 값이어야 합니다")


Text = Annotated[str, StringConstraints(strict=True, min_length=1), AfterValidator(_nonblank)]
CountryCode = Annotated[Text, Field(max_length=12)]
CalendarDate = Annotated[date, BeforeValidator(_calendar_date)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"{label} 중복은 허용하지 않습니다")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False)


class ArticleSource(str, Enum):
    KOTRA_MARKET = "KOTRA_MARKET"
    KOTRA_SHORT_NEWS = "KOTRA_SHORT_NEWS"
    HANKYUNG = "HANKYUNG"
    FNNEWS = "FNNEWS"


ALL_SOURCES = tuple(ArticleSource)
# 계약상의 배치 목적지입니다. 실제 컬렉션의 생성·내용을 보증하지 않습니다.
COLLECTION_BY_SOURCE = MappingProxyType({
    ArticleSource.KOTRA_MARKET: "kotra_article",
    ArticleSource.KOTRA_SHORT_NEWS: "kotra_article",
    ArticleSource.HANKYUNG: "kotra_article",
    ArticleSource.FNNEWS: "kotra_article",
})


class Geography(Contract):
    mode: Literal["all", "selected"]
    codes: tuple[CountryCode, ...] = ()

    @model_validator(mode="after")
    def check_selection(self):
        _unique(self.codes, "국가")
        if (self.mode == "all" and self.codes) or (self.mode == "selected" and not self.codes):
            raise ValueError("all은 빈 codes, selected는 하나 이상의 국가 코드가 필요합니다")
        return self


class HsProduct(Contract):
    # 실제 사전 존재 여부와 사용자 확정은 호출자가 보장합니다. 여기서는 형식만 검증합니다.
    hs_code: Annotated[Text, Field(pattern=r"^(?:[0-9]{4}|[0-9]{6})$")]
    hs_ver: Annotated[Text, Field(max_length=10)]


class ArticlePeriod(Contract):
    """기사 발행일 기준, 시작일·종료일 모두 포함. 기본 기간은 호출자가 확정합니다."""
    start: CalendarDate
    end: CalendarDate

    @model_validator(mode="after")
    def check_order(self):
        if self.start > self.end:
            raise ValueError("시작일은 종료일보다 늦을 수 없습니다")
        return self


class SearchConditions(Contract):
    geography: Geography
    products: tuple[HsProduct, ...]  # 품목 조건 없음은 명시적인 빈 배열
    period: ArticlePeriod
    blocked_topics: tuple[Text, ...]  # 호출자가 정규화한 최종 주제어

    @model_validator(mode="after")
    def check_duplicates(self):
        _unique(tuple((p.hs_code, p.hs_ver) for p in self.products), "품목·버전")
        _unique(self.blocked_topics, "차단 주제어")
        return self


class ReportContext(Contract):
    report_type: Literal["weekly", "monthly", "quarterly", "on_demand"]
    article_purpose: Literal["weekly_news", "local_issues"]

    @model_validator(mode="after")
    def check_purpose(self):
        if (self.report_type == "weekly") != (self.article_purpose == "weekly_news"):
            raise ValueError("주간은 weekly_news, 그 외 리포트는 local_issues 용도를 사용합니다")
        return self


SourceSelection = tuple[ArticleSource, ...] | Literal["ALL"]


class RagRequest(Contract):
    request_id: Text
    query: Text
    use_case: Literal["chat", "local_issue", "report"]
    conditions: SearchConditions
    source_codes: SourceSelection = "ALL"
    limit: PositiveInt | None = None
    report: ReportContext | None = None

    @property
    def target_count(self) -> int | None:
        if self.use_case in ("chat", "local_issue") or (self.report and self.report.report_type == "weekly"):
            return self.limit if self.limit is not None else 5
        return self.limit

    @model_validator(mode="after")
    def check_use_case(self):
        if self.source_codes != "ALL":
            if not self.source_codes:
                raise ValueError("요청 출처는 비어 있을 수 없습니다")
            _unique(self.source_codes, "요청 출처")
        if self.use_case != "report" and self.report is not None:
            raise ValueError("report 정보는 리포트 호출에만 사용합니다")
        if self.use_case == "chat" and self.limit not in (None, 5):
            raise ValueError("챗봇의 요청 기사 수는 5건입니다")
        if self.use_case == "local_issue":
            if self.target_count > 7:
                raise ValueError("현지 이슈는 최대 7건입니다")
            if self.conditions.geography.mode == "all" and not self.conditions.products:
                raise ValueError("현지 이슈는 국가 또는 품목 조건이 필요합니다")
        if self.use_case == "report":
            if self.report is None:
                raise ValueError("리포트 종류와 기사 제공 용도가 필요합니다")
            if self.report.article_purpose == "local_issues":
                if self.limit not in (3, 4, 5):
                    raise ValueError("리포트 현지 이슈 요청 건수는 호출자가 3~5 중 지정합니다")
            else:
                if self.target_count > 7:
                    raise ValueError("주간 리포트는 기본 5건, 최대 7건입니다")
                period = self.conditions.period
                if period.start.weekday() != 0 or period.end != period.start + timedelta(days=6):
                    raise ValueError("주간 기사 기간은 월요일부터 일요일까지입니다")
        return self


class ArticleKey(Contract):
    source_code: ArticleSource
    external_id: Annotated[Text, Field(max_length=100)]

    @property
    def key(self) -> tuple[str, str]:
        return self.source_code.value, self.external_id


class EvidenceSpan(Contract):
    evidence_id: Text
    chunk_id: Text
    quote: Text


class SummarySentence(Contract):
    text: Text
    evidence_ids: Annotated[tuple[Text, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def check_ids(self):
        _unique(self.evidence_ids, "문장 근거 ID")
        return self


class ArticleEvidence(ArticleKey):
    article_id: PositiveInt
    title: Text
    countries: tuple[CountryCode, ...]
    country_role: Literal["source_tag", "mentioned"] = Field(default_factory=lambda data:
        "mentioned" if data.get("source_code") in (ArticleSource.HANKYUNG, ArticleSource.FNNEWS) else "source_tag")
    published_date: CalendarDate
    source_url: HttpUrl
    body_status: Literal["available", "missing"]
    content_version: Text | None = None
    evidence: tuple[EvidenceSpan, ...] = ()
    summary: tuple[SummarySentence, ...] = ()

    @model_validator(mode="after")
    def check_references(self):
        expected_role = "mentioned" if self.source_code in (ArticleSource.HANKYUNG, ArticleSource.FNNEWS) else "source_tag"
        if self.country_role != expected_role:
            raise ValueError("출처별 국가 태그의 의미를 변경할 수 없습니다")
        _unique(self.countries, "기사 국가")
        ids = tuple(e.evidence_id for e in self.evidence)
        _unique(ids, "기사 근거 ID")
        if self.body_status == "missing" and (self.evidence or self.summary):
            raise ValueError("본문 미확보 기사에 본문 근거나 요약을 붙일 수 없습니다")
        if any(set(s.evidence_ids) - set(ids) for s in self.summary):
            raise ValueError("요약은 같은 기사의 유효한 근거 ID를 참조해야 합니다")
        return self


class RagFailure(Contract):
    stage: Literal["input", "index", "query_embedding", "search", "evidence", "summary", "validation", "timeout"]
    code: Text  # 예외 원문·인증 정보 대신 공개 가능한 코드
    source_code: ArticleSource | None = None


class RagResponse(Contract):
    request_id: Text
    use_case: Literal["chat", "local_issue", "report"]
    status: Literal["ok", "no_results", "insufficient_results", "insufficient_evidence", "partial", "failed"]
    applied_conditions: SearchConditions
    requested_source_codes: SourceSelection = "ALL"
    applied_source_codes: tuple[ArticleSource, ...] = ALL_SOURCES
    target_count: PositiveInt | None
    returned_count: Annotated[StrictInt, Field(ge=0)]
    articles: tuple[ArticleEvidence, ...]
    completed_sources: tuple[ArticleSource, ...]
    failures: tuple[RagFailure, ...] = ()
    reason: Text | None = None

    @model_validator(mode="after")
    def check_consistency(self):
        _unique(tuple(a.key for a in self.articles), "기사 식별자")
        _unique(tuple(a.article_id for a in self.articles), "기사 ID")
        _unique(self.completed_sources, "완료 출처")
        _unique(self.applied_source_codes, "적용 출처")
        if self.requested_source_codes != 'ALL' and set(self.applied_source_codes) != set(self.requested_source_codes):
            raise ValueError('요청 출처 범위를 변경할 수 없습니다')
        if set(self.completed_sources) - set(self.applied_source_codes) or any(a.source_code not in self.applied_source_codes for a in self.articles):
            raise ValueError('요청 범위 밖의 기사·완료 출처입니다')
        _unique(tuple(e.evidence_id for a in self.articles for e in a.evidence), "응답 근거 ID")
        if self.returned_count != len(self.articles):
            raise ValueError("반환 건수는 고유 기사 수와 같아야 합니다")
        if self.target_count is not None and self.returned_count > self.target_count:
            raise ValueError("반환 건수는 요청 건수를 넘을 수 없습니다")
        if self.use_case == "chat" and self.target_count != 5:
            raise ValueError("챗봇 목표 건수는 5입니다")
        if self.use_case == "local_issue" and (self.target_count is None or self.target_count > 7):
            raise ValueError("현지 이슈 목표 건수는 1~7입니다")
        if self.status != "ok" and self.reason is None:
            raise ValueError("정상 완료 외 상태에는 사유가 필요합니다")
        if self.status in ("partial", "failed"):
            if not self.failures:
                raise ValueError("실패 상태에는 실패 단계·코드가 필요합니다")
            if (self.status == "partial") != bool(self.articles):
                raise ValueError("사용 가능한 기사가 있으면 partial, 없으면 failed입니다")
        else:
            if self.failures or set(self.completed_sources) != set(self.applied_source_codes) or not self.applied_source_codes:
                raise ValueError("적용 출처가 모두 정상 처리되어야 결과 없음·부족 또는 정상 완료를 판단합니다")
        if self.status == "no_results" and self.articles:
            raise ValueError("no_results에는 기사를 반환할 수 없습니다")
        if self.status in ("ok", "insufficient_results"):
            if not self.articles or any(not a.summary for a in self.articles):
                raise ValueError("정상 근거 기사와 요약이 필요합니다")
        if self.status == "ok" and self.target_count is not None and self.returned_count != self.target_count:
            raise ValueError("목표 건수보다 적으면 insufficient_results를 사용합니다")
        if self.status == "insufficient_results":
            if self.target_count is None or not 0 < self.returned_count < self.target_count:
                raise ValueError("insufficient_results는 목표보다 적은 양수 건수에 사용합니다")
        if self.status == "insufficient_evidence" and self.articles and all(a.summary for a in self.articles):
            raise ValueError("근거 부족 상태에는 요약을 보류한 기사가 있어야 합니다")
        if self.use_case == "chat" and any(len(a.summary) > 2 for a in self.articles):
            raise ValueError("챗봇은 기사별 1~2문장 요약입니다")
        return self


def validate_response_for(request: RagRequest, response: RagResponse) -> RagResponse:
    """호출 조건 보존 확인. 실제 원문 일치·검색 관련성은 후속 단계에서 검증합니다."""
    if response.request_id != request.request_id or response.use_case != request.use_case:
        raise ValueError("다른 요청의 응답입니다")
    if response.applied_conditions != request.conditions:
        raise ValueError("요청의 확정 조건을 변경할 수 없습니다")
    if response.requested_source_codes != request.source_codes:
        raise ValueError("요청 출처 선택을 변경할 수 없습니다")
    if response.target_count != request.target_count:
        raise ValueError("요청과 응답의 목표 건수가 다릅니다")
    return response
