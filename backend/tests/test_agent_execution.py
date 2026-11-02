import unittest
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, JSON, event, select, update
from backend.app.agents.budget import new_round
from backend.app.agents.execution import ExecutionStore
from backend.app.api.database import Database
from backend.app.api.common import ApiError


class ExecutionTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        meta = MetaData()
        session = Table('chat_session', meta, Column('session_id', Integer, primary_key=True),
                        Column('user_id', Integer))
        self.messages = Table('chat_message', meta, Column('message_id', Integer, primary_key=True),
            Column('session_id', Integer), Column('role', String), Column('result_json', JSON))
        meta.create_all(self.engine)
        self.job = {'version': 1, 'job_id': 7, 'job_type': 'API_CHAT', 'target_key': '1',
                    'subject_user_id': 1, 'claim_token': 1, 'status': 'RUNNING',
                    'payload_json': {}, 'agent': new_round('round', 1)}
        with self.engine.begin() as c:
            c.execute(session.insert().values(session_id=1, user_id=1))
            c.execute(self.messages.insert().values(message_id=7, session_id=1, role='USER',
                      result_json={'_execution': self.job}))
        self.database = Database(self.engine)
        self.database.metadata = meta
        self.execution = ExecutionStore(self.database, self.job)
        self.active = 0
        event.listen(self.engine, 'begin', self.begin)
        event.listen(self.engine, 'commit', self.end)
        event.listen(self.engine, 'rollback', self.end)

    def begin(self, connection): self.active += 1
    def end(self, connection): self.active -= 1

    def test_work_runs_after_reservation_commit_without_open_transaction(self):
        def external(timeout):
            self.assertEqual(self.active, 0)
            self.assertEqual(timeout, 10)
            ledger = self.execution.read()['agent']
            self.assertEqual(ledger['charged_seconds'], 10)
            self.assertEqual(ledger['reservation']['node'], 'planner')
            return 'result'
        ticks = iter([100, 103])
        result = self.execution.call('planner', 10, external, clock=lambda: next(ticks))
        self.assertEqual(result, 'result')
        ledger = self.execution.read()['agent']
        self.assertEqual(ledger['charged_seconds'], 3)
        self.assertIsNone(ledger['reservation'])
        self.assertEqual(len(ledger['settled_call_ids']), 1)

    def test_cancellation_during_work_prevents_settlement(self):
        def external(timeout):
            self.assertEqual(self.active, 0)
            with self.engine.begin() as c:
                payload = c.scalar(select(self.messages.c.result_json))
                payload['_execution']['status'] = 'CANCELLED'
                c.execute(update(self.messages).values(result_json=payload))
        with self.assertRaises(ApiError) as error:
            self.execution.call('planner', 10, external)
        self.assertEqual(error.exception.code, 'JOB_CLAIM_LOST')
        with self.engine.connect() as c:
            job = c.scalar(select(self.messages.c.result_json))['_execution']
        self.assertEqual(job['status'], 'CANCELLED')
        self.assertEqual(job['agent']['charged_seconds'], 10)

    def test_failed_external_call_is_settled_without_hiding_error(self):
        def external(timeout): raise RuntimeError('provider unavailable')
        ticks = iter([100, 102])
        with self.assertRaisesRegex(RuntimeError, 'provider unavailable'):
            self.execution.call('planner', 10, external, clock=lambda: next(ticks))
        self.assertEqual(self.execution.read()['agent']['charged_seconds'], 2)
