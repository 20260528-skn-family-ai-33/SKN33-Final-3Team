from contextlib import redirect_stdout
import copy
from datetime import datetime, timezone
import io
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from types import SimpleNamespace

from pydantic import ValidationError

from backend.app.services.rag.evaluation import (
    CaseResult, EvaluationDataset, ExistingCollectionsOnly, RetrievalRun, RunConfiguration,
    evaluate, fingerprint, matches_fingerprint, main, record_legacy_baseline, write_new_json,
)
from backend.app.schemas.rag import ALL_SOURCES
from backend.app.services.rag.baseline import rebuild_legacy
from backend.app.services.rag.evaluate_service import record_retrieval
from backend.app.services.rag.evaluation_drafts import prepare_drafts
from backend.app.services.rag.store import COLLECTIONS, MODEL, GenerationStore
from backend.tests.rag_fixtures import configuration_payload, evaluation_payload
from backend.tests.test_rag_pipeline import FakeGateway, document, vectors


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
    def test_current_dataset_fingerprint_changes_with_request(self):
        value = evaluation_payload()
        dataset = EvaluationDataset.model_validate(value)
        digest = fingerprint(dataset)
        self.assertTrue(matches_fingerprint(dataset, digest))
        value['cases'][0]['request']['query'] = '다른 질문'
        self.assertFalse(matches_fingerprint(EvaluationDataset.model_validate(value), digest))

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
        self.assertEqual(evaluate(dataset, run)["split"], "holdout")

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
        self.assertNotIn("acceptance_status", report)
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
            self.assertNotIn("acceptance_status", json.loads(before))
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1]), {"output": str(score_path)})
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


class EvaluationExecutionTest(unittest.TestCase):
    def test_unreviewed_actual_holdout_is_rejected_before_search(self):
        payload = evaluation_payload()
        payload.update(synthetic=False, split='holdout')
        dataset = EvaluationDataset.model_validate(payload)
        configuration = RunConfiguration.model_validate(configuration_payload(finalized=True))
        with self.assertRaisesRegex(ValueError, 'holdout_review_required_before_execution'):
            record_retrieval(dataset, None, None, configuration, seconds_per_request=5)

    def test_reconstruction_is_separate_and_hash_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path(__file__).parents[1] / 'app/scheduler/jobs/news_embed.py'
            relative = 'backend/app/scheduler/jobs/news_embed.py'
            saved = root/'snapshot'/relative
            saved.parent.mkdir(parents=True)
            saved.write_bytes(source.read_bytes())
            (root/'snapshot/manifest.json').write_text(json.dumps({'files': {
                relative: hashlib.sha256(saved.read_bytes()).hexdigest()}}))
            rows = root/'rows.json'
            rows.write_text(json.dumps([{'article_id':1, 'source_code':'KOTRA_MARKET', 'external_id':'synthetic-1',
                'title':'합성 기사', 'body_masked':'재구성 연결 검증용 합성 본문', 'published_date':'2026-09-15',
                'countries':['US'], 'keywords':['배터리'], 'category':''}]))
            report = rebuild_legacy(root/'snapshot', rows, root/'baseline', vectors)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['kind'], 'reconstructed_baseline')
            self.assertFalse(report['historical_measurement'])
            self.assertEqual(report['result']['articles'], 1)
            with self.assertRaises(FileExistsError):
                rebuild_legacy(root/'snapshot', rows, root/'baseline', vectors)
            saved.write_text(saved.read_text()+'\n# changed\n')
            with self.assertRaisesRegex(ValueError, 'preserved_code_hash_mismatch'):
                rebuild_legacy(root/'snapshot', rows, root/'other', vectors)
            self.assertFalse((root/'other').exists())

    def test_recording_pins_generation_and_preserves_execution_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GenerationStore(Path(directory)/'index', dimensions=8)
            report = store.publish([document(n, external_id=f'synthetic-{n}') for n in range(1,5)],
                                   ALL_SOURCES, vectors, vocabulary={})
            configuration = RunConfiguration.model_validate(configuration_payload(
                corpus_revision=report['generation'], embedding_model=MODEL, collections=COLLECTIONS))
            dataset = EvaluationDataset.model_validate(evaluation_payload())
            run = record_retrieval(dataset, store, FakeGateway(), configuration, seconds_per_request=5)
            self.assertTrue(all(r.outcome == 'recorded' and len(r.retrieved) == 4 for r in run.results))
            class FailingGateway(FakeGateway):
                def embed(self, texts, context):
                    raise TimeoutError('private failure detail')
            failed = record_retrieval(dataset, store, FailingGateway(), configuration, seconds_per_request=5)
            self.assertTrue(all(r.outcome == 'error' and not r.retrieved and r.error_code == 'TimeoutError'
                                for r in failed.results))
            self.assertNotIn('private failure detail', failed.model_dump_json())
            with self.assertRaisesRegex(ValueError, 'corpus_revision_mismatch'):
                record_retrieval(dataset, store, FakeGateway(), configuration.model_copy(update={'corpus_revision':'other'}),
                                 seconds_per_request=5)
            with self.assertRaisesRegex(ValueError, 'runtime_configuration_mismatch'):
                record_retrieval(dataset, store, FakeGateway(), configuration.model_copy(update={'chunk_chars':500}),
                                 seconds_per_request=5)

    def test_recorded_service_run_can_be_scored_with_final_configuration(self):
        # 합성 구현 시험: 실제 정답셋이나 품질 합격을 주장하지 않습니다.
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from backend.app.services.rag.evaluate_service import record_retrieval
        from backend.app.services.rag.store import COLLECTIONS,MODEL
        from backend.app.services.rag.evaluation import RunConfiguration
        from backend.tests.rag_fixtures import configuration_payload
        data=dataset_one(split='holdout', synthetic=False, reviewed=True, review_reference='synthetic-test')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);p=root/'generations/revision/vocabulary.json';p.parent.mkdir(parents=True)
            p.write_text('{}',encoding='utf-8')
            client=Mock();snapshot=SimpleNamespace(generation='revision',client=client)
            store=SimpleNamespace(root=root,snapshot=lambda:snapshot)
            config=RunConfiguration(**configuration_payload(finalized=True,embedding_model=MODEL,
                corpus_revision='revision',collections=list(COLLECTIONS)))
            responses=[SimpleNamespace(failures=[],candidates=[SimpleNamespace(article=c.relevant_articles[0])]) for c in data.cases]
            with patch('backend.app.services.rag.evaluate_service.search',side_effect=responses):
                run=record_retrieval(data,store,Mock(),config,seconds_per_request=30)
            client.close.assert_called_once()
            self.assertEqual(evaluate(data,run)['macro_recall_at_5'], 1)
            self.assertNotIn('acceptance_status', evaluate(data,run))

class EvaluationSizeTest(unittest.TestCase):
    def test_metrics_accept_variable_sizes_and_never_return_pass_fail(self):
        for count in (3, 150):
            payload = evaluation_payload()
            template = payload['cases']
            payload['cases'] = []
            for i in range(count):
                row = copy.deepcopy(template[i % len(template)])
                row['case_id'] = f'case-{i}'
                row['request']['request_id'] = f'request-{i}'
                payload['cases'].append(row)
            payload.update(split='holdout', synthetic=False, reviewed=True,
                           review_reference='synthetic-test-review')
            dataset = EvaluationDataset.model_validate(payload)
            for hits in (0, count - 1, count):
                with self.subTest(count=count, hits=hits):
                    rows = [case_result(c, retrieved=c.relevant_articles) if i < hits
                            else case_result(c, outcome='error', error_code='TimeoutError')
                            for i, c in enumerate(dataset.cases)]
                    report = evaluate(dataset, run_for(dataset, rows, finalized=True))
                    self.assertEqual(report['case_count'], count)
                    self.assertAlmostEqual(report['macro_recall_at_5'], hits / count)
                    self.assertAlmostEqual(report['micro_recall_at_5'], hits / count)
                    self.assertEqual(report['failed_or_unsupported_cases'], count - hits)
                    for field in ('acceptance_status', 'acceptance_note', 'required_question_count',
                                  'required_recall_at_5'):
                        self.assertNotIn(field, report)

    def test_metrics_keep_local_issue_and_single_source_requests(self):
        payload = evaluation_payload()
        for case in payload['cases']:
            case['request']['use_case'] = 'local_issue'
            case['relevant_articles'] = [{'source_code':'KOTRA_MARKET', 'external_id':'synthetic-gold'}]
        dataset = EvaluationDataset.model_validate(payload)
        rows = [case_result(c, retrieved=c.relevant_articles) for c in dataset.cases]
        report = evaluate(dataset, run_for(dataset, rows))
        self.assertEqual(report['gold_sources'], ['KOTRA_MARKET'])
        self.assertEqual(report['macro_recall_at_5'], 1)

    def test_wrong_collection_contract_is_rejected_before_search(self):
        dataset = dataset_one()
        config = RunConfiguration(**configuration_payload(embedding_model=MODEL,
                                  collections=['unsupported_collection']))
        with patch('backend.app.services.rag.evaluate_service.search') as search:
            with self.assertRaisesRegex(ValueError, 'runtime_configuration_mismatch'):
                record_retrieval(dataset, None, None, config, seconds_per_request=5)
        search.assert_not_called()


class DraftCreationTest(unittest.TestCase):
    def articles(self, count):
        return [{'source_code':ALL_SOURCES[n % 4].value, 'external_id':str(n),
                 'title':f'합성 기사 {n}', 'published_date':'2026-09-15',
                 'source_url':f'https://example.org/{n}', 'body':f'검수 도구 테스트용 본문 {n}',
                 'countries':['US']} for n in range(count)]

    def test_real_article_drafts_keep_answers_unjudged_and_disjoint(self):
        for dev_count, final_count in ((3, 7), (4, 150)):
            with self.subTest(counts=(dev_count, final_count)), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / 'draft'
                manifest = prepare_drafts(self.articles(dev_count + final_count), output,
                                          dev_count=dev_count, final_count=final_count)
                self.assertEqual(manifest['missing_sources'], [])
                review = json.loads((output / 'review.json').read_text())
                self.assertEqual(len(review), dev_count + final_count)
                self.assertEqual((manifest['dev_cases'], manifest['final_cases']), (dev_count, final_count))
                for split, count in (('dev', dev_count), ('holdout', final_count)):
                    dataset = EvaluationDataset.model_validate_json((output / (split + '.json')).read_text())
                    self.assertEqual(len(dataset.cases), count)
                    self.assertFalse(dataset.reviewed)
                    self.assertTrue(all(c.relevant_articles is None for c in dataset.cases))
                dev = {(x['candidate_article']['source_code'], x['candidate_article']['external_id'])
                       for x in review if x['split'] == 'dev'}
                final = {(x['candidate_article']['source_code'], x['candidate_article']['external_id'])
                         for x in review if x['split'] == 'holdout'}
                self.assertFalse(dev & final)

    def test_draft_cli_uses_explicit_counts_and_rejects_invalid_counts(self):
        from backend.app.services.rag.evaluation_drafts import main as draft_main
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            articles = root / 'articles.jsonl'
            articles.write_text('\n'.join(json.dumps(a) for a in self.articles(10)))
            output = root / 'draft'
            with redirect_stdout(io.StringIO()) as stream:
                draft_main(['--articles', str(articles), '--output', str(output),
                            '--dev-count', '3', '--final-count', '7'])
            manifest = json.loads(stream.getvalue())
            self.assertEqual((manifest['dev_cases'], manifest['final_cases']), (3, 7))
            for dev_count, final_count in ((0, 7), (3, 0), (True, 7), (3, -1)):
                with self.subTest(counts=(dev_count, final_count)):
                    with self.assertRaisesRegex(ValueError, 'positive_question_counts_required'):
                        prepare_drafts(self.articles(10), root / 'invalid',
                                       dev_count=dev_count, final_count=final_count)
            self.assertFalse((root / 'invalid').exists())


if __name__ == "__main__":
    unittest.main()
