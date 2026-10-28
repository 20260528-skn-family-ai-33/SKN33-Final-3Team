from contextlib import redirect_stdout
import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from pydantic import ValidationError

from backend.app.services.rag.evaluation import (
    CaseResult, EvaluationDataset, ExistingCollectionsOnly, RetrievalRun, RunConfiguration,
    evaluate, fingerprint, main, record_legacy_baseline, write_new_json,
)
from backend.tests.rag_fixtures import configuration_payload, evaluation_payload


def dataset_one(**changes):
    value = evaluation_payload()
    value["cases"] = value["cases"][:1]
    value.update(changes)
    return EvaluationDataset.model_validate(value)


def chunk(source="KOTRA_MARKET", external_id="synthetic-1"):
    return ("chunk", 0.1, "본문은 실행 기록에 보존하지 않음", {"source_code": source, "external_id": external_id})


def run_for(dataset, results, **configuration):
    return RetrievalRun(run_id="synthetic-run", dataset_fingerprint=fingerprint(dataset),
                        recorded_at=datetime.now(timezone.utc), results=results,
                        configuration=RunConfiguration(**configuration_payload(**configuration)))


def case_result(case, **changes):
    payload = dict(case_id=case.case_id, request_id=case.request.request_id,
                   outcome="recorded", retrieved=(), latency_ms=1)
    payload.update(changes)
    return CaseResult(**payload)


class BaselineTest(unittest.TestCase):
    def test_duplicate_chunks_preserve_first_article_rank_without_backfill(self):
        dataset = dataset_one()
        search = Mock(return_value=[chunk(), chunk(), chunk("FNNEWS", "same"),
                                    chunk("HANKYUNG", "same"), chunk()])
        run = record_legacy_baseline(dataset, search, RunConfiguration(**configuration_payload()))
        self.assertEqual([a.key for a in run.results[0].retrieved],
                         [("KOTRA_MARKET", "synthetic-1"), ("FNNEWS", "same"), ("HANKYUNG", "same")])
        self.assertEqual(run.results[0].raw_chunk_count, 5)
        search.assert_called_once_with(dataset.cases[0].request.query, n=5, country="US",
                                       date_from="2026-09-01", date_to="2026-09-30")
        self.assertNotIn("본문은", run.model_dump_json())

    def test_all_countries_and_unsupported_conditions_are_honest(self):
        payload = evaluation_payload()
        payload["cases"] = payload["cases"][:1]
        conditions = payload["cases"][0]["request"]["conditions"]
        conditions.update(geography={"mode": "all"}, products=[{"hs_code": "0101", "hs_ver": "HS2022"}],
                          blocked_topics=["배터리"])
        dataset = EvaluationDataset.model_validate(payload)
        search = Mock(return_value=[])
        run = record_legacy_baseline(dataset, search, RunConfiguration(**configuration_payload()))
        self.assertIsNone(search.call_args.kwargs["country"])
        self.assertIn("hs_metadata_filter_not_supported", run.results[0].limitations)
        self.assertIn("user_block_not_supported", run.results[0].limitations)

    def test_multiple_countries_and_nonchat_do_not_silently_shrink_request(self):
        for modification in ("countries", "use_case"):
            payload = evaluation_payload()
            payload["cases"] = payload["cases"][:1]
            request = payload["cases"][0]["request"]
            if modification == "countries":
                request["conditions"]["geography"]["codes"] = ["US", "PL"]
            else:
                request["use_case"] = "local_issue"
            search = Mock()
            run = record_legacy_baseline(EvaluationDataset.model_validate(payload), search,
                                         RunConfiguration(**configuration_payload()))
            search.assert_not_called()
            self.assertEqual(run.results[0].outcome, "unsupported")

    def test_exception_is_failure_without_exception_message(self):
        dataset = dataset_one()
        search = Mock(side_effect=RuntimeError("private diagnostic"))
        run = record_legacy_baseline(dataset, search, RunConfiguration(**configuration_payload()))
        self.assertEqual(run.results[0].error_code, "RuntimeError")
        self.assertNotIn("private diagnostic", run.model_dump_json())
        self.assertEqual(evaluate(dataset, run)["macro_recall_at_5"], 0)

    def test_bad_source_metadata_is_not_a_valid_empty_result(self):
        run = record_legacy_baseline(dataset_one(), Mock(return_value=[chunk("unknown")]),
                                     RunConfiguration(**configuration_payload()))
        self.assertEqual(run.results[0].outcome, "error")

    def test_holdout_guard_runs_before_search_and_also_before_score(self):
        dataset = dataset_one(split="holdout")
        search = Mock(return_value=[])
        with self.assertRaises(ValueError):
            record_legacy_baseline(dataset, search, RunConfiguration(**configuration_payload()))
        search.assert_not_called()
        with self.assertRaises(ValueError):
            evaluate(dataset, run_for(dataset, []))
        run = record_legacy_baseline(dataset, search, RunConfiguration(**configuration_payload(finalized=True)))
        self.assertEqual(evaluate(dataset, run)["acceptance_status"], "not_assessed")

    def test_existing_collection_adapter_never_creates_collection(self):
        client = Mock()
        adapter = ExistingCollectionsOnly(client)
        self.assertIs(adapter.get_or_create_collection("total_article", metadata={"x": 1}),
                      client.get_collection.return_value)
        client.get_collection.assert_called_once_with("total_article", embedding_function=None)
        client.get_or_create_collection.assert_not_called()


class EvaluationTest(unittest.TestCase):
    def test_article_recall_counts_multiple_answers_and_missing_execution(self):
        payload = evaluation_payload()
        payload["cases"] = payload["cases"][:2]
        payload["cases"][0]["relevant_articles"].append({"source_code": "FNNEWS", "external_id": "other"})
        dataset = EvaluationDataset.model_validate(payload)
        result = case_result(dataset.cases[0], retrieved=dataset.cases[0].relevant_articles[:1])
        report = evaluate(dataset, run_for(dataset, [result]))
        self.assertEqual(report["macro_recall_at_5"], 0.25)
        self.assertAlmostEqual(report["micro_recall_at_5"], 1 / 3)
        self.assertEqual(report["not_run_cases"], 1)
        self.assertEqual(report["cases"][1]["recall_at_5"], 0)

    def test_unlabeled_no_answer_and_error_are_separate(self):
        payload = evaluation_payload()
        for case in payload["cases"][:3]:
            case["relevant_articles"] = []
        payload["cases"][3]["relevant_articles"] = None
        dataset = EvaluationDataset.model_validate(payload)
        results = [case_result(dataset.cases[0]),
                   case_result(dataset.cases[1], outcome="error", error_code="TimeoutError"),
                   case_result(dataset.cases[3])]
        report = evaluate(dataset, run_for(dataset, results))
        self.assertEqual((report["no_answer_cases"], report["correctly_empty_cases"], report["unlabeled_cases"]), (3, 1, 1))
        self.assertIsNone(report["macro_recall_at_5"])
        self.assertFalse(report["cases"][1]["correctly_empty"])
        self.assertFalse(report["cases"][2]["correctly_empty"])

    def test_only_first_five_unique_articles_count(self):
        dataset = dataset_one()
        retrieved = [{"source_code": "FNNEWS", "external_id": str(i)} for i in range(5)]
        retrieved.append(dataset.cases[0].relevant_articles[0])
        result = case_result(dataset.cases[0], retrieved=retrieved)
        self.assertEqual(evaluate(dataset, run_for(dataset, [result]))["macro_recall_at_5"], 0)

    def test_perfect_synthetic_score_is_not_a_quality_pass(self):
        dataset = dataset_one()
        result = case_result(dataset.cases[0], retrieved=dataset.cases[0].relevant_articles)
        report = evaluate(dataset, run_for(dataset, [result]))
        self.assertEqual(report["macro_recall_at_5"], 1)
        self.assertEqual(report["acceptance_status"], "not_assessed")
        self.assertTrue(report["synthetic"])

    def test_changed_dataset_or_wrong_request_cannot_reuse_run(self):
        dataset = dataset_one()
        run = run_for(dataset, [])
        changed = dataset.model_dump(mode="json")
        changed["cases"][0]["request"]["query"] += " 변경"
        with self.assertRaises(ValueError):
            evaluate(EvaluationDataset.model_validate(changed), run)
        result = case_result(dataset.cases[0], request_id="another-request")
        with self.assertRaises(ValueError):
            evaluate(dataset, run_for(dataset, [result]))
        result = case_result(dataset.cases[0], case_id="another-case")
        with self.assertRaises(ValueError):
            evaluate(dataset, run_for(dataset, [result]))

    def test_duplicate_labels_cases_and_results_are_rejected(self):
        payload = evaluation_payload()
        payload["cases"].append(copy.deepcopy(payload["cases"][0]))
        with self.assertRaises(ValidationError):
            EvaluationDataset.model_validate(payload)
        payload = evaluation_payload()
        payload["cases"][0]["relevant_articles"] *= 2
        with self.assertRaises(ValidationError):
            EvaluationDataset.model_validate(payload)
        dataset = dataset_one()
        with self.assertRaises(ValidationError):
            case_result(dataset.cases[0], retrieved=dataset.cases[0].relevant_articles * 2)
        result = case_result(dataset.cases[0])
        with self.assertRaises(ValidationError):
            run_for(dataset, [result, result])

    def test_cli_validate_and_offline_score_preserve_outputs(self):
        dataset = dataset_one()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as output:
            base = Path(folder)
            dataset_path, run_path, score_path = (base / name for name in ("dataset.json", "run.json", "score.json"))
            write_new_json(dataset_path, dataset.model_dump(mode="json"))
            result = case_result(dataset.cases[0], retrieved=dataset.cases[0].relevant_articles)
            write_new_json(run_path, run_for(dataset, [result]).model_dump(mode="json"))
            self.assertEqual(main(["validate", "--dataset", str(dataset_path)]), 0)
            self.assertFalse(json.loads(output.getvalue())["baseline_measured"])
            args = ["score", "--dataset", str(dataset_path), "--run", str(run_path), "--output", str(score_path)]
            self.assertEqual(main(args), 0)
            before = score_path.read_bytes()
            self.assertEqual(json.loads(before)["acceptance_status"], "not_assessed")
            with self.assertRaises(FileExistsError):
                main(args)
            with self.assertRaises(FileExistsError):
                write_new_json(score_path, {"replacement": True})
            self.assertEqual(score_path.read_bytes(), before)

    def test_capture_refuses_missing_index_before_importing_search(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            write_new_json(path / "dataset.json", dataset_one().model_dump(mode="json"))
            with self.assertRaisesRegex(ValueError, "기존 Chroma"):
                main(["capture-legacy", "--dataset", str(path / "dataset.json"), "--chroma-path", str(path / "missing"),
                      "--configuration-id", "synthetic", "--corpus-revision", "synthetic",
                      "--output", str(path / "run.json")])
            self.assertFalse((path / "missing").exists())


if __name__ == "__main__":
    unittest.main()
