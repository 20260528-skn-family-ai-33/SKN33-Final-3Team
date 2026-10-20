"""공통 비중 계산 및 CSV 왕복 없는 적재 경로의 동등성 검증."""
import copy
import unittest
from decimal import Decimal
from unittest import mock
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.collectors.un_data import pipeline,transform
from backend.app.scheduler.jobs import un_data_refresh as job,un_data_load as loader

class RefactorTest(unittest.TestCase):
    setUp=fixture.MonthlyRefreshTest.setUp
    tearDown=fixture.MonthlyRefreshTest.tearDown
    run_job=fixture.MonthlyRefreshTest.run_job
    snapshot=fixture.MonthlyRefreshTest.snapshot
    checkpoint=fixture.MonthlyRefreshTest.checkpoint

    def test_share_rounding_zero_and_null(self):
        for world,korea,expected in [('3','1','33.333333'),('128','1','0.781250'),('200000000','1','0.000001'),('10','0','0.000000'),('0','0',None),(None,'1',None),('1',None,None)]:
            with self.subTest(world=world,korea=korea):
                value=lambda n:None if n is None else Decimal(n)
                self.assertEqual(pipeline.share(value(world),value(korea)),value(expected))

    def test_monthly_does_not_read_audit_csv(self):
        with mock.patch.object(job,'read_rows',side_effect=AssertionError('CSV reread')):
            self.run_job()
        self.assertEqual(len(self.snapshot()),3)
        files=list(self.state.glob('runs/*/datasets/*/un_data.csv'))
        self.assertEqual(len(files),2)
        self.assertEqual(sum(len(loader.read_rows(p)) for p in files),3)

    def test_invalid_in_memory_output_rejected_before_write(self):
        original=transform.transform
        def invalid(*args,**kwargs):
            result=original(*args,**kwargs)
            result[0][0][7]=Decimal('999')
            return result
        checkpoint=self.checkpoint()
        with mock.patch.object(job,'transform',side_effect=invalid):
            with self.assertRaisesRegex(ValueError,'비중 계산 불일치'):self.run_job()
        self.assertEqual(self.snapshot(),[])
        self.assertEqual(checkpoint,self.checkpoint())

    def test_direct_rows_equal_csv_for_null_zero_and_rounding(self):
        values=[['M','2024-01-01','AL','010121','HS2022',Decimal('3'),Decimal('1'),Decimal('33.333333'),'CIF'],
                ['A','2023-01-01','AT','010129','HS2022',None,Decimal('0'),None,'FOB']]
        path=self.path/'converted.csv';job.write_csv(path,pipeline.HEAD,values)
        self.assertEqual(loader.validate(loader.to_rows(values)),loader.read_rows(path))
        for malformed in [values[0][:-1],values[0]+['extra']]:
            with self.assertRaisesRegex(ValueError,'컬럼 개수'):loader.to_rows([malformed])

    def test_target_pairs_reject_outside_scope_without_mutating_config(self):
        cfg=copy.deepcopy(fixture.CFG);before=copy.deepcopy(cfg)
        raw=[{k:transform.scalar(v) for k,v in fixture.raw().items()}]
        transform.transform(raw,cfg)
        self.assertEqual(cfg,before)
        cfg['target_pairs']=[pair for pair in cfg['target_pairs'] if pair!=['8','010121']]
        with self.assertRaisesRegex(ValueError,'country/product scope'):transform.transform(raw,cfg)

if __name__=='__main__':unittest.main()
