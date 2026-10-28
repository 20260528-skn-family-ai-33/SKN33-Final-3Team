"""보존한 검색 코드와 고정 입력으로 별도 기준선 색인을 재구성합니다."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from backend.app.services.rag.evaluation import write_new_json
from backend.app.services.rag.store import persistent_client


def load_preserved_search(snapshot):
    snapshot = Path(snapshot)
    module_path = snapshot/'backend/app/scheduler/jobs/news_embed.py'
    manifest = json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
    code_hash = hashlib.sha256(module_path.read_bytes()).hexdigest()
    if code_hash != manifest['files']['backend/app/scheduler/jobs/news_embed.py']:
        raise ValueError('preserved_code_hash_mismatch')
    spec = importlib.util.spec_from_file_location('preserved_news_embed', module_path)
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    return legacy, code_hash


def rebuild_legacy(snapshot, rows_path, output, embed):
    snapshot, rows_path, output = Path(snapshot), Path(rows_path), Path(output)
    if output.exists():
        raise FileExistsError('기존 기준선은 덮어쓰지 않습니다')
    legacy, code_hash = load_preserved_search(snapshot)
    rows = json.loads(rows_path.read_text(encoding='utf-8'))
    output.mkdir(parents=True)
    report = {'kind':'reconstructed_baseline', 'status':'building', 'code_sha256':code_hash,
              'input_sha256':hashlib.sha256(rows_path.read_bytes()).hexdigest(),
              'historical_measurement':False}
    client = None
    failure = None
    try:
        client = persistent_client(output/'chroma')
        result = legacy.sync(rows, legacy.get_collection(client, legacy.ARTICLE_COLLECTION), embed)
        report.update(status='complete',result=result)
    except Exception as exc:
        failure = exc
        report.update(status='failed',error_code=type(exc).__name__)
        raise
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                report['cleanup_errors'] = [{'resource':'client','error_type':type(exc).__name__}]
                if failure is None:
                    report['status'] = 'partial'
        try:
            write_new_json(output/'reconstruction.json',report)
        except Exception as exc:
            if failure is None:
                raise
            failure.add_note('reconstruction report write failed: ' + type(exc).__name__)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--rows',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from backend.app.scheduler.jobs.news_embed import openai_embed
    provider = openai_embed()
    failure = None
    try:
        result = rebuild_legacy(args.snapshot,args.rows,args.output,provider)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            provider.close()
        except Exception as exc:
            if failure is not None:
                failure.add_note('embedding cleanup failed: ' + type(exc).__name__)
            else:
                result.setdefault('cleanup_errors', []).append({'resource':'embedding','error_type':type(exc).__name__})
                result['status'] = 'partial'
                from backend.app.services.rag.store import atomic_json
                atomic_json(args.output/'reconstruction.json', result)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status']=='complete' else 1


if __name__=='__main__': raise SystemExit(main())
