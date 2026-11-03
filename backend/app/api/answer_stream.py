"""Stream only the persisted, verified final answer. Never expose model drafts."""
import json
from fastapi.responses import StreamingResponse
from backend.app.api.common import require


def final_answer_stream(job):
    answer=(job.get('result_json') or {}).get('answer')
    require(job['status']=='SUCCEEDED' and answer is not None,
            '검증된 최종 답변이 아직 준비되지 않았습니다.','ANSWER_NOT_READY',409)
    text=answer.get('text','')
    def events():
        for offset in range(0,len(text),32):
            yield 'event: delta\ndata: '+json.dumps({'text':text[offset:offset+32]},ensure_ascii=False)+'\n\n'
        yield 'event: done\ndata: {}\n\n'
    return StreamingResponse(events(),media_type='text/event-stream',headers={
        'Cache-Control':'private, no-store','X-Accel-Buffering':'no'})
