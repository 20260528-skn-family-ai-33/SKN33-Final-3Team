"""Bounded article workers share one deadline and preserve retrieval order."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event, Lock

from openai import APITimeoutError

from backend.app.services.rag.runtime import RagDeadlineExceeded, RagInferenceUnavailable


def review_articles(candidates, review, context, target_count):
    """Return completed results only; cancellation never waits for a running SDK call.

    The synchronous SDK cannot forcibly cancel an in-flight HTTP request. Its
    timeout is the shared remaining budget; cancellation prevents subsequent
    calls, cache writes after that call, and publication into the returned trace.
    """
    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix='rag-article')
    lock, stopped = Lock(), Event()
    completed, active, children = {}, {}, []
    next_rank, stop_reason = 0, None

    def work(rank, candidate, child):
        value, error = None, None
        try:
            child.remaining()
            child.event('summary_candidate', article_key=candidate.article.identity)
            value = review(candidate, child)
            child.remaining()
        except Exception as exc:
            error = exc
        with lock:
            if stopped.is_set():
                return
            if error is None:
                try:
                    child.remaining()
                except Exception as exc:
                    error, value = exc, None
            if error is None:
                child.event('candidate_review_complete', accepted=bool(value[1]))
            else:
                child.event('candidate_review_failed', error_type=type(error).__name__)
            completed[rank] = (candidate, value, error)

    try:
        while True:
            with lock:
                ready = dict(completed)
            active = {f: rank for f, rank in active.items() if rank not in ready}
            errors = [row[2] for row in ready.values() if row[2] is not None]
            if any(isinstance(e, (RagDeadlineExceeded, APITimeoutError, TimeoutError)) for e in errors):
                stop_reason = 'timeout'
                break
            if any(isinstance(e, RagInferenceUnavailable) for e in errors):
                stop_reason = 'inference_unavailable'
                break
            accepted = sum(row[1] is not None and bool(row[1][1]) for row in ready.values())
            if accepted >= target_count:
                stop_reason = 'target_met'
                break
            try:
                context.remaining()
            except RagDeadlineExceeded:
                stop_reason = 'timeout'
                break
            # Do not speculate beyond the remaining result slots. Once five
            # articles pass, no earlier or later worker is left to change rank.
            slots = min(3 - len(active), target_count - accepted - len(active))
            for _ in range(min(slots, len(candidates) - next_rank)):
                context.remaining()
                candidate = candidates[next_rank]
                child = context.child(article_key=candidate.article.identity, candidate_rank=next_rank+1)
                children.append(child)
                future = executor.submit(work, next_rank, candidate, child)
                active[future] = next_rank
                next_rank += 1
            if not active:
                break
            try:
                remaining = context.remaining()
            except RagDeadlineExceeded:
                stop_reason = 'timeout'
                break
            wait(active, timeout=remaining, return_when=FIRST_COMPLETED)
    except RagDeadlineExceeded:
        stop_reason = 'timeout'
    finally:
        with lock:
            stopped.set()
            result = [completed[i] for i in sorted(completed)]
        for child in children:
            child.cancelled.set()
            context.absorb(child)
        for future in active:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        context.events.sort(key=lambda e: e['elapsed_ms'])
        context.event('parallel_review_finished', workers=3, started=next_rank,
                      completed=sum(error is None for _, _, error in result), stop_reason=stop_reason)
    return result, stop_reason
