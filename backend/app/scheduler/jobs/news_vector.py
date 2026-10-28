"""공통 색인의 격리 자식 진입점. 임의 날짜 수집이나 예약 실행은 하지 않습니다."""
import argparse
import json
import os
from pathlib import Path
import sys
from filelock import FileLock

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from backend.app.scheduler.collectors.trade_news.news.run_lifecycle import atomic_json, require_current_job


def execute(data_dir, target_date, result_file, *, root=None, indexer=None):
    from backend.app.scheduler.jobs.news_embed import run_sources
    sources = ['HANKYUNG', 'FNNEWS']
    identity = {'run_id':os.environ.get('TRADE_NEWS_JOB_RUN_ID'), 'target_date':target_date}
    with FileLock(str(Path(data_dir)/'crawler.lock')):
        require_current_job(data_dir, 'vector')
        def checkpoint(value):
            atomic_json(result_file, {**value, **identity})
        checkpoint({'status':'running', 'sources':sources})
        result = (indexer or run_sources)(sources, root=root, progress=checkpoint)
        result.update(identity)
        checkpoint(result)
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, required=True)
    p.add_argument('--target-date', required=True)
    p.add_argument('--result-file', type=Path, required=True)
    p.add_argument('--root', type=Path)
    a = p.parse_args()
    result = execute(a.data_dir, a.target_date, a.result_file, root=a.root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status']=='complete' else 1


if __name__=='__main__':
    raise SystemExit(main())
