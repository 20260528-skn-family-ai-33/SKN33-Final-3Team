"""20개 합성 요청·상태에서 RAG patch 소유권 검증. 실제 서비스 State 검수와 구분합니다."""
import copy
from pathlib import Path
import tempfile
import time
import unittest

from backend.app.schemas.rag import ALL_SOURCES
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded
from backend.app.services.rag.service import RagService, rag_node
from backend.app.services.rag.store import GenerationStore
from backend.app.services.rag.vocabulary import Vocabulary
from backend.tests.rag_fixtures import request_payload
from backend.tests.test_rag_pipeline import FakeGateway, document, vectors


class StateMatrixTest(unittest.TestCase):
    def test_twenty_state_paths_preserve_other_results(self):
        cases = [
            ('chat_success','chat','ok'), ('local_success','local_issue','ok'),
            ('weekly_success','report','ok'), ('monthly_success','report','ok'),
            ('quarterly_success','report','ok'), ('on_demand_success','report','ok'),
            ('local_limit_seven','local_issue','ok'), ('all_countries','chat','ok'),
            ('multiple_countries','local_issue','ok'), ('date_boundary','local_issue','ok'),
            ('no_country_match','local_issue','no_results'), ('no_period_match','local_issue','no_results'),
            ('blocked_all','local_issue','no_results'), ('result_shortage','local_issue','insufficient_results'),
            ('evidence_withheld','local_issue','insufficient_evidence'), ('source_partial','local_issue','partial'),
            ('summary_partial','local_issue','partial'), ('summary_failed','local_issue','failed'),
            ('index_failed','local_issue','failed'), ('global_deadline','local_issue','global_deadline'),
        ]
        self.results=[]
        with tempfile.TemporaryDirectory() as folder:
            for name,use_case,expected in cases:
                with self.subTest(case=name):
                    store=GenerationStore(Path(folder)/name,dimensions=8)
                    gateway=FakeGateway()
                    service=RagService(store,Vocabulary(),gateway)
                    request=request_payload(use_case,request_id='state-'+name)
                    docs=[document(n) for n in range(1,8)]
                    sources=ALL_SOURCES
                    if use_case=='report':
                        report_type=name.removesuffix('_success')
                        request.update(report={'report_type':report_type,'article_purpose':'weekly_news' if report_type=='weekly' else 'local_issues'})
                        if report_type=='weekly':request['conditions']['period']={'start':'2026-09-14','end':'2026-09-20'}
                        else:request['limit']=3
                    if name=='local_limit_seven':request['limit']=7
                    if name=='all_countries':request['conditions']['geography']={'mode':'all','codes':[]}
                    if name=='multiple_countries':request['conditions']['geography']['codes']=['US','PL']
                    if name=='date_boundary':request['conditions']['period']={'start':'2026-09-15','end':'2026-09-15'}
                    if name=='no_country_match':request['conditions']['geography']['codes']=['PL']
                    if name=='no_period_match':request['conditions']['period']={'start':'2026-08-01','end':'2026-08-31'}
                    if name=='blocked_all':request['conditions']['blocked_topics']=['배터리']
                    if name=='result_shortage':docs=docs[:2]
                    if name=='evidence_withheld':gateway.empty=True
                    if name=='source_partial':
                        sources=['KOTRA_MARKET'];docs=[docs[0]]
                        request['source_codes']=[s.value for s in ALL_SOURCES]
                    if name=='summary_partial':
                        # 문서 header가 바뀌어 순위가 달라져도 한 건 성공·나머지 실패를 재현합니다.
                        summarize=gateway.summarize
                        def partial_summary(request,candidate,context):
                            if gateway.summary_calls:raise RuntimeError('synthetic_summary_failure')
                            return summarize(request,candidate,context)
                        gateway.summarize=partial_summary
                    if name=='summary_failed':gateway.fail={d.key for d in docs}
                    if name!='index_failed':store.publish(docs,sources,vectors)
                    state={'rag_request':request,'trade_result':{'amount':123,'rows':[1,2]},
                           'country_result':{'confirmed':['US']},'report_result':{'saved':True}}
                    before=copy.deepcopy(state)
                    context=ExecutionContext(time.monotonic()+(-1 if name=='global_deadline' else 30))
                    if name=='global_deadline':
                        with self.assertRaises(RagDeadlineExceeded):rag_node(state,service,context)
                        status='global_deadline';owned=True
                    else:
                        patch=rag_node(state,service,context)
                        status=patch['rag_result']['status'];owned=set(patch)=={'rag_result','rag_trace'}
                        self.assertEqual(status,expected,(name,patch['rag_result']))
                        self.assertTrue(owned)
                    self.assertEqual(state,before)
                    self.results.append({'case':name,'synthetic':True,'expected':expected,'actual':status,
                                         'other_state_preserved':state==before,'owned_fields_only':owned})


if __name__=='__main__':unittest.main()
