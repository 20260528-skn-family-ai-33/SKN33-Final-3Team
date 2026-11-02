import unittest
from unittest.mock import patch
from sqlalchemy import Table, Column, String, Integer, JSON, DateTime, select, update, func
from backend.tests import test_agent_execution
from backend.tests.test_agent_state import state
from backend.app.agents.result_store import ResultStore, fingerprint
from backend.app.agents.state import ToolResult
from backend.app.api.common import ApiError, now


class ResultStoreTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_agent_execution.ExecutionTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.execution = self.fixture.execution
        self.table = Table('api_snapshot', self.fixture.database.metadata,
            Column('snapshot_id', String, primary_key=True), Column('user_id', Integer),
            Column('kind', String), Column('payload_json', JSON), Column('created_at', DateTime),
            Column('expires_at', DateTime))
        self.table.create(self.fixture.engine)
        self.store = ResultStore(self.execution)
        self.state = state()
        self.state.input.user_id = 1
        self.state.input.job_id = '7'
        self.state.input.session_id = '1'
        self.step = self.state.plan.tasks[0].steps[1]
        self.state.plan.tasks[0].steps[0].status = 'DONE'
        self.versions = {'trade': '2026-10-release-1'}
        self.key = self.fingerprint()

    def fingerprint(self):
        return fingerprint(self.state, self.step, tool_version='1', versions=self.versions, source_results=[])

    def save(self):
        return self.store.save(self.state, self.step, ToolResult(status='OK', digest={'total': 5}),
            rows=[{'amount_usd': 5}], meta={'kind': 'TREND'}, conditions={},
            input_fingerprint=self.key, source_results=[], versions=self.versions)

    def test_atomic_save_and_reuse_with_identical_input(self):
        tool = self.save()
        self.assertEqual(self.store.reuse(self.state, self.step, self.key), tool)
        self.assertEqual(self.execution.read()['agent']['step_results'][self.step.step_id]['result_id'], tool.result_id)

    def test_fingerprint_changes_with_inputs_but_not_attempt(self):
        self.step.attempts = 1
        self.assertEqual(self.fingerprint(), self.key)
        self.state.plan.tasks[0].custom_query = '분기별 수출'
        self.assertNotEqual(self.fingerprint(), self.key)
        self.assertIsNone(fingerprint(self.state, self.step, tool_version='1', versions={}, source_results=[]))
        with self.assertRaises(ValueError):
            fingerprint(self.state, self.step, tool_version='1', versions={'v': float('nan')}, source_results=[])

    def test_pointer_failure_rolls_back_result_table(self):
        with patch('backend.app.agents.execution.jobs._save_chat', side_effect=RuntimeError('commit boundary')):
            with self.assertRaises(RuntimeError): self.save()
        with self.fixture.engine.connect() as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(self.table)), 0)
        self.assertEqual(self.execution.read()['agent']['step_results'], {})

    def test_expired_result_is_miss_but_wrong_owner_is_access_error(self):
        tool = self.save()
        with self.fixture.engine.begin() as c:
            c.execute(update(self.table).where(self.table.c.snapshot_id == tool.result_id).values(expires_at=now()))
        self.assertIsNone(self.store.reuse(self.state, self.step, self.key))
        with self.fixture.engine.begin() as c:
            c.execute(update(self.table).values(user_id=2, expires_at=None))
        with self.assertRaises(ApiError): self.store.reuse(self.state, self.step, self.key)

    def test_partial_upstream_is_saved_and_restored(self):
        self.state.results['t1.check'] = ToolResult(status='OK', completeness='PARTIAL', limitations=['일부 조건 누락'])
        tool = self.save()
        self.assertEqual(tool.completeness, 'PARTIAL')
        restored = self.store.reuse(self.state, self.step, self.key)
        self.assertEqual(restored.limitations, ['일부 조건 누락'])

    def test_legacy_pointer_and_tampered_metadata_are_misses(self):
        tool = self.save()
        with self.fixture.engine.begin() as c:
            payload = c.scalar(select(self.table.c.payload_json))
            payload['meta']['input_fingerprint'] = 'different'
            c.execute(update(self.table).values(payload_json=payload))
        self.assertIsNone(self.store.reuse(self.state, self.step, self.key))
        self.execution.transition(lambda value: {**value, 'step_results': {self.step.step_id: tool.result_id}})
        self.assertIsNone(self.store.reuse(self.state, self.step, self.key))
