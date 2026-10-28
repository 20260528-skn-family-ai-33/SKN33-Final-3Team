import copy
from datetime import datetime
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from backend.app.schemas.rag import (
    ALL_SOURCES, COLLECTION_BY_SOURCE, ArticleEvidence, ArticleKey, ArticlePeriod,
    Geography, HsProduct, RagRequest, RagResponse, validate_response_for,
)
from backend.tests.rag_fixtures import article_payload, request_payload, response_payload


class RagRequestTest(unittest.TestCase):
    def test_defaults_and_local_seven(self):
        for kind in ("chat", "local_issue"):
            self.assertEqual(RagRequest.model_validate(request_payload(kind)).target_count, 5)
        self.assertEqual(RagRequest.model_validate(request_payload(limit=7)).target_count, 7)

    def test_invalid_limits_are_not_coerced_or_clamped(self):
        for value in (0, -1, 8, True, 5.0, "5"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                RagRequest.model_validate(request_payload(limit=value))
        with self.assertRaises(ValidationError):
            RagRequest.model_validate(request_payload("chat", limit=7))

    def test_missing_country_is_not_all(self):
        value = request_payload("chat")
        del value["conditions"]["geography"]
        with self.assertRaises(ValidationError):
            RagRequest.model_validate(value)
        value["conditions"]["geography"] = {"mode": "all"}
        self.assertEqual(RagRequest.model_validate(value).conditions.geography.codes, ())

    def test_geography_selection_invariants(self):
        for value in ({"mode": "selected", "codes": []}, {"mode": "all", "codes": ["US"]},
                      {"mode": "selected", "codes": ["US", "US"]}, {"mode": "selected", "codes": [123]}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Geography.model_validate(value)
        self.assertEqual(Geography(mode="selected", codes=("US", "PL")).codes, ("US", "PL"))

    def test_local_issue_accepts_country_only_or_product_only(self):
        RagRequest.model_validate(request_payload())
        value = request_payload()
        value["conditions"]["geography"] = {"mode": "all"}
        with self.assertRaises(ValidationError):
            RagRequest.model_validate(value)
        value["conditions"]["products"] = [{"hs_code": "3304", "hs_ver": "HS2022"}]
        RagRequest.model_validate(value)

    def test_hs_preserves_leading_zero_and_version_pairs(self):
        value = request_payload()
        value["conditions"]["products"] = [
            {"hs_code": "0101", "hs_ver": "HS2017"}, {"hs_code": "010121", "hs_ver": "HS2022"}]
        request = RagRequest.model_validate(value)
        self.assertEqual([(p.hs_code, p.hs_ver) for p in request.conditions.products],
                         [("0101", "HS2017"), ("010121", "HS2022")])
        for product in ({"hs_code": "330499"}, {"hs_code": 330499, "hs_ver": "HS2022"},
                        {"hs_code": "3304990000", "hs_ver": "HS2022"}, {"hs_code": "3304", "hs_ver": " "}):
            with self.subTest(product=product), self.assertRaises(ValidationError):
                HsProduct.model_validate(product)

    def test_duplicate_products_and_blocks_are_rejected(self):
        for field, value in (("products", [{"hs_code": "3304", "hs_ver": "HS2022"}] * 2),
                             ("blocked_topics", ["전기차", "전기차"])):
            payload = request_payload()
            payload["conditions"][field] = value
            with self.assertRaises(ValidationError):
                RagRequest.model_validate(payload)

    def test_dates_are_exact_calendar_dates_and_inclusive(self):
        period = ArticlePeriod(start="2024-02-29", end="2024-02-29")
        self.assertEqual(period.start, period.end)
        for start, end in (("2026-09-30", "2026-09-01"), ("2026-02-29", "2026-03-01"),
                           ("20260901", "2026-09-30"), (0, "2026-09-30"),
                           (datetime(2026, 9, 1), "2026-09-30")):
            with self.subTest(start=start), self.assertRaises(ValidationError):
                ArticlePeriod(start=start, end=end)

    def test_report_local_count_is_explicit_and_can_later_return_fewer(self):
        payload = request_payload("report", report={"report_type": "monthly", "article_purpose": "local_issues"})
        with self.assertRaises(ValidationError):
            RagRequest.model_validate(payload)
        payload["limit"] = 3
        request = RagRequest.model_validate(payload)
        response = RagResponse.model_validate(response_payload(payload, "insufficient_results", 2))
        validate_response_for(request, response)

    def test_weekly_defaults_to_five_and_allows_seven(self):
        payload = request_payload("report", report={"report_type": "weekly", "article_purpose": "weekly_news"})
        payload["conditions"]["period"] = {"start": "2026-10-12", "end": "2026-10-18"}
        self.assertEqual(RagRequest.model_validate(payload).target_count, 5)
        payload["limit"] = 7
        self.assertEqual(RagRequest.model_validate(payload).target_count, 7)
        payload["limit"] = 8
        with self.assertRaises(ValidationError):
            RagRequest.model_validate(payload)
        payload["limit"] = 7
        payload["conditions"]["period"]["end"] = "2026-10-19"
        with self.assertRaises(ValidationError):
            RagRequest.model_validate(payload)

    def test_report_purpose_cannot_be_mixed(self):
        for payload in (
            request_payload(report={"report_type": "monthly", "article_purpose": "local_issues"}),
            request_payload("report", limit=5),
            request_payload("report", limit=5, report={"report_type": "weekly", "article_purpose": "local_issues"}),
        ):
            with self.assertRaises(ValidationError):
                RagRequest.model_validate(payload)

    def test_extra_conditions_and_blank_query_are_rejected(self):
        for payload in (request_payload(query="  "), request_payload(unconfirmed_filter=True)):
            with self.assertRaises(ValidationError):
                RagRequest.model_validate(payload)

    def test_four_sources_keep_shared_newspaper_destination(self):
        self.assertEqual(len(COLLECTION_BY_SOURCE), 4)
        self.assertEqual(COLLECTION_BY_SOURCE[ALL_SOURCES[0]], "total_article")
        self.assertEqual(COLLECTION_BY_SOURCE[ALL_SOURCES[1]], "total_article")
        self.assertEqual(COLLECTION_BY_SOURCE[ALL_SOURCES[2]], "total_article")
        self.assertEqual(COLLECTION_BY_SOURCE[ALL_SOURCES[3]], "total_article")
        self.assertNotEqual(ArticleKey(source_code="HANKYUNG", external_id="1").key,
                            ArticleKey(source_code="FNNEWS", external_id="1").key)


class RagResponseTest(unittest.TestCase):
    def test_round_trip_and_condition_preservation(self):
        request = RagRequest.model_validate(request_payload())
        response = RagResponse.model_validate(response_payload())
        self.assertEqual(RagResponse.model_validate_json(response.model_dump_json()), response)
        self.assertEqual(validate_response_for(request, response), response)

    def test_actual_count_and_duplicate_articles(self):
        for field in ("article_id", "external_id"):
            value = response_payload()
            value["articles"][4][field] = value["articles"][0][field]
            with self.assertRaises(ValidationError):
                RagResponse.model_validate(value)
        value = response_payload()
        value["returned_count"] = 4
        with self.assertRaises(ValidationError):
            RagResponse.model_validate(value)

    def test_fewer_articles_are_preserved_without_padding(self):
        response = RagResponse.model_validate(response_payload(status="insufficient_results", count=3))
        self.assertEqual((response.target_count, response.returned_count), (5, 3))
        for status, count in (("ok", 3), ("insufficient_results", 5), ("insufficient_results", 0), ("ok", 6)):
            with self.subTest(status=status, count=count), self.assertRaises(ValidationError):
                RagResponse.model_validate(response_payload(status=status, count=count))

    def test_no_results_requires_every_source_to_have_completed(self):
        value = response_payload(status="no_results", count=0)
        RagResponse.model_validate(value)
        value["completed_sources"].pop()
        with self.assertRaises(ValidationError):
            RagResponse.model_validate(value)

    def test_partial_and_failed_keep_failure_information(self):
        failure = [{"stage": "search", "code": "index_unavailable", "source_code": "FNNEWS"}]
        value = response_payload(status="partial", count=1, completed_sources=["KOTRA_MARKET"], failures=failure)
        self.assertEqual(RagResponse.model_validate(value).returned_count, 1)
        value["status"] = "ok"
        with self.assertRaises(ValidationError):
            RagResponse.model_validate(value)
        value = response_payload(status="failed", count=0, completed_sources=[], failures=failure)
        RagResponse.model_validate(value)
        del value["failures"]
        with self.assertRaises(ValidationError):
            RagResponse.model_validate(value)

    def test_missing_body_cannot_be_summarized(self):
        article = article_payload()
        article["body_status"] = "missing"
        with self.assertRaises(ValidationError):
            ArticleEvidence.model_validate(article)
        article.update(evidence=[], summary=[])
        response = response_payload(status="insufficient_evidence", count=1, articles=[article])
        RagResponse.model_validate(response)

    def test_summary_requires_same_article_evidence(self):
        for evidence_ids in ([], ["other-article"], ["evidence-1", "evidence-1"]):
            article = article_payload()
            article["summary"][0]["evidence_ids"] = evidence_ids
            with self.assertRaises(ValidationError):
                ArticleEvidence.model_validate(article)

    def test_chat_summary_has_at_most_two_sentences(self):
        request = request_payload("chat")
        value = response_payload(request)
        value["articles"][0]["summary"] *= 3
        with self.assertRaises(ValidationError):
            RagResponse.model_validate(value)

    def test_source_url_is_required_and_must_be_http(self):
        for url in ("", "file:///private/article", "javascript:alert(1)"):
            value = article_payload()
            value["source_url"] = url
            with self.assertRaises(ValidationError):
                ArticleEvidence.model_validate(value)

    def test_response_cannot_broaden_conditions_or_change_request(self):
        request = RagRequest.model_validate(request_payload())
        for field, replacement in (("request_id", "another-request"), ("target_count", 7)):
            value = response_payload(status="insufficient_results", count=3)
            value[field] = replacement
            with self.assertRaises(ValueError):
                validate_response_for(request, RagResponse.model_validate(value))
        value = response_payload()
        value["applied_conditions"] = copy.deepcopy(value["applied_conditions"])
        value["applied_conditions"]["period"]["start"] = "2026-01-01"
        with self.assertRaises(ValueError):
            validate_response_for(request, RagResponse.model_validate(value))

    def test_published_examples_are_valid_contracts(self):
        path = Path(__file__).resolve().parents[2] / "docs/rag/contract_examples.json"
        examples = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(examples["synthetic"])
        self.assertEqual({x["response"]["status"] for x in examples["cases"]},
                         {"ok", "no_results", "insufficient_results", "insufficient_evidence", "partial", "failed"})
        for item in examples["cases"]:
            validate_response_for(RagRequest.model_validate(item["request"]), RagResponse.model_validate(item["response"]))


if __name__ == "__main__":
    unittest.main()
