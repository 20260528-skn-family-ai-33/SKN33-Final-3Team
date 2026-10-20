"""예외와 traceback을 보관해도 CSV 파일은 검증 종료 즉시 닫혀야 합니다."""
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from backend.app.scheduler.collectors.un_data import pipeline
from backend.app.scheduler.jobs.un_data_load import read_rows

class CsvFileLifetimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'input.csv'
        self.row=['M','2024-01-01','AL','010121','HS2022','100','25','25.000000','CIF']

    def write(self,rows,head=None):
        with self.path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(head or pipeline.HEAD);writer.writerows(rows)

    def check_closed(self,operation,error_type=None):
        opened=[];open_file=io.open;retained=None
        def track(*args,**kwargs):
            handle=open_file(*args,**kwargs);opened.append(handle);return handle
        try:
            with mock.patch('io.open',side_effect=track):
                if error_type is None:result=operation()
                else:
                    try:operation()
                    except error_type as error:retained=error
                    else:self.fail('expected failure')
            self.assertTrue(opened)
            if error_type:self.assertIsNotNone(retained.__traceback__)
            self.assertTrue(all(f.closed for f in opened),'traceback 보관 중에도 파일은 닫혀야 함')
            # 현재 OS에서 교체·삭제도 확인하며 예외 객체는 이 시점까지 유지합니다.
            replacement=self.path.with_name('replacement.csv');replacement.write_text('replacement',encoding='utf-8')
            replacement.replace(self.path);self.path.unlink()
            return result if error_type is None else retained
        finally:
            # 실패하는 수정 전 코드에서도 테스트용 파일 핸들은 정리합니다.
            for f in opened:f.close()

    def test_success_closes_file(self):
        self.write([self.row])
        rows=self.check_closed(lambda:pipeline.validate_csv(self.path))
        self.assertEqual(len(rows),1)

    def test_validation_failures_close_with_retained_traceback(self):
        cases=[([self.row],list(reversed(pipeline.HEAD))),
               ([self.row[:5]+['-1']+self.row[6:]],None),
               ([self.row,self.row],None),([],None),
               ([self.row[:7]+['99','CIF']],None)]
        for rows,head in cases:
            with self.subTest(rows=rows,head=head):
                self.write(rows,head)
                self.check_closed(lambda:pipeline.validate_csv(self.path),ValueError)

    def test_decoding_failure_closes_file(self):
        self.path.write_bytes(b'\xff\xfe invalid UTF-8')
        self.check_closed(lambda:pipeline.validate_csv(self.path),UnicodeDecodeError)

    def test_reader_failure_closes_file(self):
        self.write([self.row])
        def broken_reader(handle):
            yield dict(zip(pipeline.HEAD,self.row))
            raise OSError('read failure')
        with mock.patch.object(pipeline.csv,'DictReader',side_effect=broken_reader):
            self.check_closed(lambda:pipeline.validate_csv(self.path),OSError)

    def test_keyboard_interrupt_closes_file(self):
        self.write([self.row])
        def interrupted(records):
            next(records)
            raise KeyboardInterrupt('interrupted')
        with mock.patch.object(pipeline,'validate_records',side_effect=interrupted):
            self.check_closed(lambda:pipeline.validate_csv(self.path),KeyboardInterrupt)

    def test_loader_entry_also_closes_on_failure(self):
        self.write([self.row,self.row])
        self.check_closed(lambda:read_rows(self.path),ValueError)

if __name__=='__main__':unittest.main()
