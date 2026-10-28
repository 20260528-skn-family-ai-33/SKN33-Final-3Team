"""기존 해외시장뉴스 수집·정제 결과를 받아 본문 검색 반영까지 연결합니다."""
import argparse
import json
from pathlib import Path
from uuid import uuid4

from backend.app.services.rag.store import atomic_json

ROOT = Path(__file__).resolve().parents[4]


def run(raw_path, clean_path, *, db_url=None, stages=None, log_dir=ROOT/'data/rag/market_runs'):
    if stages is None:
        from backend.app.scheduler.jobs import news_load, keyword_topic_load, news_embed
        stages = [('articles', lambda: news_load.run(raw_path, clean_path, db_url=db_url)),
                  ('keyword_catalog', lambda: keyword_topic_load.run(clean_path, db_url=db_url)),
                  ('keyword_index', lambda: news_embed.run(db_url=db_url, keywords_only=True)),
                  ('article_index', lambda: _article_index(raw_path, db_url))]
    report={'status':'running','steps':[]}
    try:
        for name, action in stages:
            result=action()
            report['steps'].append({'stage':name,'success':result==0})
            if result != 0:
                report.update(status='failed',failed_stage=name)
                break
        else:
            report['status']='complete'
    except Exception as exc:
        report.update(status='failed',error_code=type(exc).__name__)
    atomic_json(Path(log_dir)/(uuid4().hex+'.json'),report)
    return report


def _article_index(raw_path, db_url):
    from backend.app.scheduler.jobs.rag_index import run as refresh
    result=refresh(['KOTRA_MARKET'],db_url=db_url,raw_market=raw_path)
    return 0 if result['status']=='complete' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw',type=Path,required=True)
    parser.add_argument('--clean',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.raw,args.clean)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status']=='complete' else 1


if __name__=='__main__':raise SystemExit(main())
