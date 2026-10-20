"""Mac에서도 경로 제한·캐시 호환성을 검증합니다. 실제 Windows 파일시스템 검증은 별도입니다."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

from backend.app.scheduler.collectors.un_data import paths
from backend.app.scheduler.collectors.un_data.continuation import CachedClient
from backend.app.scheduler.jobs import un_data_refresh as job, un_data_resume as resume
from backend.tests import test_un_data_exclusions as exclusions_fixture


class WindowsPathTest(unittest.TestCase):
    def test_windows_boundary_includes_atomic_temporary_suffix(self):
        suffix = PureWindowsPath('continuations') / ('0'*32) / 'requests' / ('0'*64+'.json.'+'0'*32+'.tmp')
        room = 259 - len(str(suffix)) - 1
        root = 'C:\\' + 'a'*(room-3)
        with mock.patch.object(paths.sys, 'platform', 'win32'):
            paths.validate_state_path(root)
            with self.assertRaisesRegex(ValueError, '260 UTF-16'):
                paths.validate_state_path(root+'a')

    def test_unicode_path_uses_utf16_units(self):
        self.assertEqual(paths.windows_path_units('C:\\한글😀'), 7)

    def test_mac_keeps_long_path_support(self):
        with mock.patch.object(paths.sys, 'platform', 'darwin'):
            paths.validate_state_path('/tmp/'+'a'*200)

    def test_reject_before_api_db_or_state_changes_for_all_entrypoints(self):
        engine=mock.Mock();client=mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/('a'*120)
            with mock.patch.object(paths.sys,'platform','win32'):
                for call in (lambda:resume.run_resumable(engine,client,'missing.json',folder),
                             lambda:job.run(engine,client,state_dir=folder),
                             lambda:job.initialize_csv(engine,state_dir=folder)):
                    with self.assertRaisesRegex(ValueError,'짧은 영속 경로'):call()
                    self.assertFalse(folder.exists())
            self.assertEqual(engine.mock_calls,[])
            self.assertEqual(client.mock_calls,[])


class CacheGenerationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'requests';self.out=Path(self.temp.name)/'out.json'
        self.client=mock.Mock();self.client.get.return_value=[{'value':1}]
        self.request={'path':'sample','params':{'reporterCode':'8'}}

    def get(self,generation):
        client=CachedClient(self.client,self.folder,job.save,generation=generation)
        return client.get(self.request['path'],self.request['params'],self.out)

    def old_cache(self,generation=None):
        digest=hashlib.sha256(json.dumps(self.request,sort_keys=True).encode()).hexdigest()
        target=self.folder if generation is None else self.folder/generation
        file=target/(digest+'.json')
        job.save(file,{'request':self.request,'rows':[{'old':True}]})
        return file

    def test_same_generation_reuses_and_full_generation_separates(self):
        first='a'*64;second='a'*63+'b'
        self.get(first);self.get(first);self.get(second)
        self.assertEqual(self.client.get.call_count,2)
        self.assertEqual(len(list(self.folder.glob('*.json'))),2)
        self.assertFalse(any(p.is_dir() for p in self.folder.iterdir()))

    def test_unversioned_existing_cache_is_reused(self):
        self.old_cache()
        self.assertEqual(self.get(None),[{'old':True}])
        self.client.get.assert_not_called()

    def test_old_generation_cache_is_reused_and_preserved(self):
        old=self.old_cache('a'*64);before=old.read_bytes()
        self.assertEqual(self.get('a'*64),[{'old':True}])
        self.client.get.assert_not_called()
        self.assertEqual(old.read_bytes(),before)
        self.assertEqual(len(list(self.folder.glob('*.json'))),1)

    def test_inaccessible_legacy_length_refetches_into_compact_cache(self):
        old=self.old_cache('a'*64)
        with mock.patch('backend.app.scheduler.collectors.un_data.continuation.windows_path_supported',return_value=False):
            self.assertEqual(self.get('a'*64),[{'value':1}])
        self.client.get.assert_called_once()
        self.assertTrue(old.exists())

    def test_generation_mismatch_fails_closed(self):
        self.get('a'*64)
        file=next(self.folder.glob('*.json'));saved=json.loads(file.read_text(encoding='utf-8'))
        saved['generation']='b'*64;job.save(file,saved)
        with self.assertRaisesRegex(ValueError,'캐시 불일치'):self.get('a'*64)
        self.assertEqual(self.client.get.call_count,1)


class ApprovalPathTest(unittest.TestCase):
    # 실제 파일 열기·폴더 생성·교체에 Windows 길이 제한을 주입합니다.
    # OS 자체 에뮬레이션은 아니며 동일 시나리오에서 구형 실패/수정본 성공을 비교합니다.
    def run_with_windows_limit(self, root, legacy=False):
        case=exclusions_fixture.ExclusionResumeTest('test_revoke_refetches_failed_response_and_preserves_fences')
        case.setUp();self.addCleanup(case.tearDown)
        original_units=paths.windows_path_units
        original_open=Path.open;original_mkdir=Path.mkdir;original_replace=os.replace
        lengths=[]

        def units(path):
            try:
                relative=Path(str(path).replace('\\','/')).relative_to(case.state)
            except ValueError:
                return original_units(path)
            return original_units(root/PureWindowsPath(relative))

        def check(path):
            length=units(path);lengths.append(length)
            if length>=260:raise OSError('simulated Windows MAX_PATH exceeded')

        def opened(path,*args,**kwargs):
            check(path);return original_open(path,*args,**kwargs)

        def mkdir(path,*args,**kwargs):
            check(path);return original_mkdir(path,*args,**kwargs)

        def replace(source,target,*args,**kwargs):
            check(source);check(target);return original_replace(source,target,*args,**kwargs)

        def old_layout(client,folder,save,generation=None):
            return CachedClient(client,folder/generation if generation else folder,save)

        with mock.patch.object(paths.sys,'platform','win32'), \
             mock.patch.object(paths,'windows_path_units',side_effect=units), \
             mock.patch.object(Path,'open',opened),mock.patch.object(Path,'mkdir',mkdir), \
             mock.patch.object(os,'replace',replace), \
             mock.patch.object(resume,'CachedClient',old_layout if legacy else CachedClient):
            if legacy:
                with self.assertRaisesRegex(OSError,'MAX_PATH'):
                    case.test_revoke_refetches_failed_response_and_preserves_fences()
            else:
                case.test_revoke_refetches_failed_response_and_preserves_fences()
                self.assertLess(max(lengths),260)
        return max(lengths)

    def test_original_layout_fails_but_compact_layout_completes_same_flow(self):
        root=PureWindowsPath('C:/Users/jihwan/Desktop/SKN33-Final-3Team/data/un_data/runtime')
        self.assertGreaterEqual(self.run_with_windows_limit(root,legacy=True),260)
        self.assertEqual(self.run_with_windows_limit(root),225)

    def test_actual_writes_at_259_units_and_unicode_unc_root(self):
        root=PureWindowsPath('C:/Users/jihwan/Desktop/SKN33-Final-3Team/data/un_data/runtime')
        # 225자 예시보다 34단위 길어도 가장 긴 임시 파일이 259자 안에서 완료됩니다.
        self.assertEqual(self.run_with_windows_limit(PureWindowsPath(str(root)+'a'*34)),259)
        self.run_with_windows_limit(PureWindowsPath('//server/share/한글😀/runtime'))

    def test_approve_revoke_paths_fit_typical_windows_root(self):
        case=exclusions_fixture.ExclusionResumeTest('test_revoke_refetches_failed_response_and_preserves_fences')
        case.setUp();self.addCleanup(case.tearDown)
        original=job.save;written=[]
        def save(path,value):
            written.append(Path(path).relative_to(case.state))
            return original(path,value)
        with mock.patch.object(job,'save',side_effect=save):
            case.test_revoke_refetches_failed_response_and_preserves_fences()
        root=PureWindowsPath('C:/Users/jihwan/Desktop/SKN33-Final-3Team/data/un_data/runtime')
        lengths=[paths.windows_path_units(root/PureWindowsPath(p))+37 for p in written]
        self.assertLess(max(lengths),260)
        requests=[p for p in written if 'requests' in p.parts]
        self.assertTrue(requests)
        self.assertTrue(all(len(p.parts)==4 for p in requests))
