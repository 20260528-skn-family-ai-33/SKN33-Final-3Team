"""HS 검색 후보 어댑터. HTTP 요청에서는 컬렉션 생성/적재를 하지 않는다."""
from functools import lru_cache
from pathlib import Path
from hashlib import sha256
from backend.app.api.search_cache import SearchCache

MODEL='text-embedding-3-large'
COLLECTION='hs_code'
DEFAULT_PATH=Path(__file__).resolve().parents[3]/'chroma_data'
VECTOR_CACHE=SearchCache(maximum=256,ttl=300)


@lru_cache(maxsize=4)
def collection(path):
    import chromadb
    if not Path(path).exists(): raise FileNotFoundError('HS Chroma directory not prepared')
    client=chromadb.PersistentClient(path=path)
    col=client.get_collection(COLLECTION,embedding_function=None)
    if (col.metadata or {}).get('embedding_model')!=MODEL:
        raise ValueError('HS embedding model mismatch')
    return col


def candidates(query,settings,limit=5,*,depth=4):
    from openai import OpenAI
    path=str(Path(settings.get('API_HS_CHROMA_PATH',str(DEFAULT_PATH))).resolve())
    col=collection(path)
    if not col.count(): return []
    key=next((v for k,v in settings.values.items() if k.lower()=='openai_api_key' and v),None)
    if not key: raise ValueError('HS embedding key missing')
    def embed():
        with OpenAI(api_key=key,timeout=float(settings.get('API_HS_TIMEOUT_SECONDS',3)),max_retries=0) as client:
            return client.embeddings.create(model=MODEL,input=[query]).data[0].embedding
    vector=VECTOR_CACHE.get_or_load((MODEL,sha256(key.encode()).hexdigest(),query),embed)
    result=col.query(query_embeddings=[vector],n_results=min(limit,col.count()),
                     where={'$and':[{'hs_ver':'HS2022'},{'depth':depth}]},include=['metadatas','distances'])
    return [(meta['hs_code'],float(distance)) for meta,distance in zip(result['metadatas'][0],result['distances'][0])]
