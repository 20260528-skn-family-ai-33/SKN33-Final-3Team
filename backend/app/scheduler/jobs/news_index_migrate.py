"""구형 Chroma 본문 색인을 재임베딩 없이 새 세대로 복사합니다. 기본은 dry-run."""
import argparse
import json
import re
from pathlib import Path


def main(argv=None):
    from backend.app.services.rag.legacy_migration import migrate
    from backend.app.services.rag.store import resolve_root
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True,help='writer를 중지한 구형 chroma_data 경로')
    parser.add_argument('--root',type=Path,help='NEWS_VECTOR_ROOT 또는 기본 data/rag/index')
    parser.add_argument('--catalog',type=Path,required=True,help='검증된 기사·사전·DB 식별 JSON; UTF-8')
    parser.add_argument('--database-id',required=True,help='database_identity와 같은 64자리 SHA-256; 접속 URL 아님')
    parser.add_argument('--source-stopped',action='store_true',help='원본을 쓰는 모든 프로세스를 중지했음을 확인')
    parser.add_argument('--legacy-model',choices=['text-embedding-3-large'],help='원본 모델 메타데이터가 없을 때 근거를 확인하고 명시')
    parser.add_argument('--apply',action='store_true',help='검증 후 새 세대를 게시; 생략하면 target 쓰기 없음')
    parser.add_argument('--batch-size',type=int,default=256)
    args=parser.parse_args(argv)
    try:
        result=migrate(args.source,resolve_root(args.root),args.catalog,args.database_id,
                       apply=args.apply,source_stopped=args.source_stopped,legacy_model=args.legacy_model,batch_size=args.batch_size)
    except Exception as exc:
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,
                          'error_code':str(exc) if isinstance(exc,ValueError) and re.fullmatch(r'[a-z][a-z0-9_]+',str(exc)) else 'migration_validation_failed'},ensure_ascii=False))
        return 1
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
