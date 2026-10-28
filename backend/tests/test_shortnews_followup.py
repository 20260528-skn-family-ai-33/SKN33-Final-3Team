"""최신 PR32 리뷰의 독립 로직 검증. 외부 API/운영 저장소를 사용하지 않습니다."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.services import shortnews_load as load, shortnews_keywords as keywords
from backend.app.services import shortnews_analysis as analysis, shortnews_vectors as vectors
from backend.tests.test_shortnews_load import raw


class TitleTest(unittest.TestCase):
    def test_entities_and_whitespace_follow_shared_plain_title(self):
        title = '  수출&amp;middot;수입\n\t&amp;#39;전망&#39; &lt;무역&gt;  '
        with patch.object(load, 'plain_title', wraps=load.plain_title) as shared:
            self.assertEqual(load.article_row(raw(nttSj=title))['title'], "수출·수입 '전망' <무역>")
        shared.assert_called_once()

    def test_decoded_length_is_validated_before_shared_truncation(self):
        self.assertEqual(load.article_row(raw(nttSj='&middot;' * 600))['title'], '·' * 600)
        with self.assertRaisesRegex(ValueError, '600'):
            load.article_row(raw(nttSj='&middot;' * 601))
        self.assertEqual(load.article_row(raw(nttSj='가' * 600))['title'], '가' * 600)
        with self.assertRaises(ValueError): load.article_row(raw(nttSj='가' * 601))

    def test_csv_json_normalize_identically(self):
        row = raw(nttSj=' A&nbsp; B &amp;#39;한글&#39; ')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'raw.json').write_text(json.dumps([row], ensure_ascii=False), encoding='utf-8')
            with (root/'raw.csv').open('w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
            self.assertEqual(load.article_row(load.read_records(root/'raw.json')[0]),
                             load.article_row(load.read_records(root/'raw.csv')[0]))


class PagedCollection:
    def __init__(self, rows, metadata):
        self.rows, self.metadata = rows, metadata
        self.calls = []
    def count(self): return len(self.rows)
    def get(self, *, include, limit, offset):
        if limit > 5000: raise AssertionError('unbounded Chroma read')
        self.calls.append((limit, offset))
        rows = self.rows[offset:offset+limit]
        return {'ids':[r['keyword'] for r in rows], 'documents':[r['keyword'] for r in rows], 'metadatas':rows}


class PaginationTest(unittest.TestCase):
    def test_33001_candidates_are_verified_in_pages_without_reembedding(self):
        rows=tuple({'keyword':f'kw{i}', 'canonical':f'kw{i}', 'status':'PENDING',
                    'canonical_status':'PENDING','major':'','minor':''} for i in range(33001))
        snap=keywords.Catalog('a'*64,rows)
        col=PagedCollection(rows, {'hnsw:space':'cosine','embedding_model':keywords.MODEL,
                                  'dimensions':keywords.DIMENSIONS,'classification_revision':snap.revision})
        client=Mock();client.get_or_create_collection.return_value=col;client.get_collection.return_value=col
        client.list_collections.return_value=[]
        embed=Mock(side_effect=AssertionError('existing candidates must not be reembedded'))
        _, result=keywords.sync_index(snap,client,embed)
        self.assertEqual(result['existing'],33001)
        self.assertEqual(keywords.cleanup_indexes(snap,client),[])
        self.assertEqual(col.calls, [(5000,i) for i in range(0,30000,5000)]+[(3001,30000)]
                         + [(5000,i) for i in range(0,30000,5000)]+[(3001,30000)]
                         + [(5000,i) for i in range(0,30000,5000)]+[(3001,30000)])
        embed.assert_not_called()

    def test_model_dimensions_and_revision_mismatch_fail_before_reads_or_writes(self):
        snap=keywords.Catalog('a'*64,())
        good={'hnsw:space':'cosine','embedding_model':keywords.MODEL,
              'dimensions':keywords.DIMENSIONS,'classification_revision':snap.revision}
        for field,value in [('embedding_model','other'),('dimensions',1536),('classification_revision','b'*64)]:
            col=Mock(metadata={**good,field:value});client=Mock();client.get_or_create_collection.return_value=col
            with self.assertRaises(ValueError): keywords.sync_index(snap,client,Mock())
            col.get.assert_not_called(); col.upsert.assert_not_called()

    def test_truncated_or_duplicate_pages_fail(self):
        col=Mock();col.count.return_value=2
        for response in ({'ids':[], 'documents':[], 'metadatas':[]},
                         {'ids':['a','a'],'documents':['a','a'],'metadatas':[{},{}]}):
            col.get.return_value=response
            with self.assertRaises(ValueError): keywords.read_index(col)


class Utf8Test(unittest.TestCase):
    def test_cache_reader_explicitly_requests_utf8(self):
        from backend.tests.test_shortnews_analysis import AnalysisTest
        case=AnalysisTest();case.setUp();self.addCleanup(case.doCleanups)
        case.analyze()
        original=Path.read_text
        def read(path, *args, **kwargs):
            self.assertEqual(kwargs.get('encoding'),'utf-8')
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',read):
            _,cached=case.analyze()
        self.assertTrue(cached)

    def test_failed_atomic_write_closes_file_before_removing_temp(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'result.json'
            analysis.atomic_json(target,{'기존':'자료'})
            streams, removed = [], []
            create = analysis.tempfile.NamedTemporaryFile
            unlink = Path.unlink
            def opened(*args, **kwargs):
                stream = create(*args, **kwargs)
                streams.append(stream)
                return stream
            def closed_before_unlink(path, *args, **kwargs):
                for stream in streams:
                    if Path(stream.name) == path:
                        self.assertTrue(stream.closed, 'Windows에서는 열린 파일을 삭제할 수 없음')
                        removed.append(path)
                return unlink(path, *args, **kwargs)
            with patch.object(analysis.os,'fsync',side_effect=OSError('disk failure')), \
                    patch.object(analysis.tempfile,'NamedTemporaryFile',opened), \
                    patch.object(Path,'unlink',closed_before_unlink):
                with self.assertRaises(OSError): analysis.atomic_json(target,{'새':'자료'})
            self.assertEqual(len(removed), 1)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'기존':'자료'})
            self.assertEqual(list(Path(d).iterdir()),[target])


class BodyAdapterTest(unittest.TestCase):
    def test_explicit_alias_mapping_does_not_change_storage_source(self):
        self.assertEqual(vectors.internal_source('KOTRA_BREAKING'),'KOTRA_SHORT_NEWS')
        for value in ('',None,'KOTRA_MARKET'):
            with self.assertRaises(ValueError): vectors.internal_source(value)

    def test_only_requested_source_and_partial_failure_are_preserved(self):
        for status in ('complete','partial','failed'):
            result={'status':status,'sources':['KOTRA_SHORT_NEWS'],'body':{'status':'complete' if status != 'failed' else 'failed','sources':['KOTRA_SHORT_NEWS'],'collection':'kotra_article'},'keywords':{'status':'complete' if status == 'complete' else 'failed','scope':'all_database_sources','collection':'news_keyword'},'usage':{},'error_code':None}
            runner=Mock(return_value=result);embed=Mock()
            out=vectors.sync_body(db_url='sqlite://',root=Path('/synthetic-root'),embed=embed,run_sources=runner)
            self.assertEqual(out,result)
            runner.assert_called_once_with(['KOTRA_SHORT_NEWS'],db_url='sqlite://',root=Path('/synthetic-root'),embed=embed,dry_run=False,progress=None)
            embed.assert_not_called()

    def test_dry_run_forwarded_and_wrong_sources_rejected(self):
        runner=Mock(return_value={'status':'SUCCESS','sources':['KOTRA_MARKET']})
        with self.assertRaises(ValueError):
            vectors.sync_body(db_url='sqlite://',root=Path('/synthetic-root'),embed=Mock(),dry_run=True,run_sources=runner)
        self.assertTrue(runner.call_args.kwargs['dry_run'])
