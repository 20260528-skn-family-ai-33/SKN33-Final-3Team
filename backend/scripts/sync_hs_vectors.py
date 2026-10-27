"""RDS를 읽어 API-11용 HS2022 상위 분류/HS4만 기존 Chroma hs_code에 증분 적재."""
from sqlalchemy import select
from backend.app.api.config import Settings
from backend.app.api.database import Database


def main():
    import chromadb
    from backend.app.scheduler.jobs import hs_code_embed as he
    from backend.app.api.hs_vectors import DEFAULT_PATH, MODEL
    from openai import OpenAI
    settings=Settings(); settings.values['API_DB_TARGET']='rds'
    engine=settings.engine()
    try:
        with engine.connect() as c:
            from sqlalchemy import text
            c.execute(text('SET TRANSACTION READ ONLY'))
            t=Database(engine).table('hs_code',c)
            rows=[dict(r) for r in c.execute(select(t.c.hs_code,t.c.hs_ver,t.c.name_ko,t.c.original_name_ko)
                  .where(t.c.hs_ver=='HS2022',t.c.depth.in_([2,4]))).mappings()]
            c.rollback()
    finally: engine.dispose()
    key=next((v for k,v in settings.values.items() if k.lower()=='openai_api_key' and v),None)
    client=chromadb.PersistentClient(path=settings.get('API_HS_CHROMA_PATH',str(DEFAULT_PATH)))
    col=he.get_collection(client)
    with OpenAI(api_key=key,timeout=60,max_retries=2) as api:
        def embed(texts):
            return [item.embedding for item in api.embeddings.create(model=MODEL,input=texts).data]
        result=he.sync(rows,col,embed)
    print({'source_rows':len(rows),**result,'collection_count':col.count()})


if __name__=='__main__': main()
