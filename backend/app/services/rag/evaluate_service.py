"""공통 기사 검색을 기록합니다. 요약 품질 검수와 검색 Recall을 분리합니다."""
import argparse
from backend.app.services.rag.resources import close_owned
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import math
from uuid import uuid4

from backend.app.services.rag.evaluation import (
    CaseResult, EvaluationDataset, RetrievalRun, RunConfiguration, fingerprint, write_new_json, _check_holdout,
)
from backend.app.services.rag.retrieval import search
from backend.app.services.rag.runtime import ExecutionContext
from backend.app.services.rag.vocabulary import Vocabulary
from backend.app.services.rag.store import MODEL, COLLECTIONS


def record_retrieval(dataset, store, gateway, configuration, *, seconds_per_request):
    if not math.isfinite(seconds_per_request) or seconds_per_request <= 0:
        raise ValueError('positive_time_budget_required')
    _check_holdout(dataset, configuration)
    if dataset.split == 'holdout' and not dataset.synthetic and not dataset.reviewed:
        raise ValueError('holdout_review_required_before_execution')
    if (configuration.embedding_model != MODEL or configuration.chunk_chars != 1000
            or configuration.overlap_chars != 150 or set(configuration.collections) != set(COLLECTIONS)):
        raise ValueError('runtime_configuration_mismatch')
    snapshot = store.snapshot()
    primary = None
    try:
        if snapshot.generation != configuration.corpus_revision:
            raise ValueError('corpus_revision_mismatch')
        vocabulary = Vocabulary(**json.loads((store.root/'generations'/snapshot.generation/'vocabulary.json').read_text(encoding='utf-8')))
        results=[]
        for case in dataset.cases:
            started=time.monotonic()
            context=ExecutionContext(started+seconds_per_request)
            try:
                found=search(case.request,snapshot,vocabulary,gateway,context)
                if found.failures:
                    raise RuntimeError('retrieval_partial_failure')
                result=CaseResult(case_id=case.case_id,request_id=case.request.request_id,outcome='recorded',
                    retrieved=[{'source_code':c.article.source_code,'external_id':c.article.external_id} for c in found.candidates],
                    latency_ms=(time.monotonic()-started)*1000)
            except Exception as exc:
                result=CaseResult(case_id=case.case_id,request_id=case.request.request_id,outcome='error',
                                  error_code=type(exc).__name__,latency_ms=(time.monotonic()-started)*1000)
            results.append(result)
        return RetrievalRun(run_id=uuid4().hex,dataset_fingerprint=fingerprint(dataset),
                            recorded_at=datetime.now(timezone.utc),configuration=configuration,results=results)
    except BaseException as error:
        primary = error
        raise
    finally:
        close_owned([('snapshot', snapshot.client.close)], primary=primary)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--configuration',type=Path,required=True)
    parser.add_argument('--index-root',type=Path,required=True)
    parser.add_argument('--seconds-per-request',type=float,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('기존 실행 보존')
    dataset=EvaluationDataset.model_validate_json(args.dataset.read_text(encoding='utf-8'))
    configuration=RunConfiguration.model_validate_json(args.configuration.read_text(encoding='utf-8'))
    _check_holdout(dataset,configuration)
    from dotenv import dotenv_values
    from openai import OpenAI
    from backend.app.db.session import ENV_PATH
    from backend.app.services.rag.gateway import ModelGateway
    from backend.app.services.rag.store import GenerationStore
    import os
    values={**dotenv_values(ENV_PATH),**os.environ}
    api = OpenAI(api_key=values.get('OPENAI_API_KEY'),max_retries=0)
    primary = None
    try:
        run=record_retrieval(dataset,GenerationStore(args.index_root),ModelGateway(api),configuration,
                             seconds_per_request=args.seconds_per_request)
    except BaseException as error:
        primary = error
        raise
    finally:
        close_owned([('openai_client', api.close)], primary=primary)
    write_new_json(args.output,run.model_dump(mode='json'))


if __name__=='__main__':main()
