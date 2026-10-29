"""키워드 재구축을 별도 세대에서 검증하고 활성 포인터로 교체합니다."""
import json
import math
from pathlib import Path
import re
from uuid import uuid4

from backend.app.services.rag.store import atomic_json
from backend.app.services.rag.resources import closing_owned

COLLECTION = 'news_keyword'


def writer_base(path):
    """주입된 세대 client도 기본 writer와 같은 논리 저장소를 잠급니다."""
    path = Path(path).resolve()
    if (path.name == 'chroma' and path.parent.parent.name == 'generations'
            and re.fullmatch(r'[0-9a-f]{32}', path.parent.name)):
        return path.parent.parent.parent
    return path


def check_compatibility(collection, *, model, dimensions):
    """설정값으로 추정하지 않고 실제 저장 차원과 선언된 차원을 함께 확인합니다."""
    def incompatible(reason):
        error = ValueError('incompatible_keyword_embedding')
        error.add_note(reason)
        raise error
    metadata = collection.metadata or {}
    if metadata.get('embedding_model') != model:
        incompatible('keyword model mismatch; rebuild required')
    declared = metadata.get('dimensions')
    if 'dimensions' in metadata and (type(declared) is not int or declared <= 0 or declared != dimensions):
        incompatible('declared keyword dimensions mismatch; rebuild required')

    # get_collection/get_or_create_collection가 반환한 최신 Chroma 모델에는 빈 컬렉션의
    # 이전 바인딩 차원도 남습니다. 비어 있다고 미바인딩으로 간주하지 않습니다.
    unknown = object()
    get_model = getattr(collection, 'get_model', None)
    stored = getattr(get_model(), 'dimension', unknown) if callable(get_model) else unknown
    if stored is not unknown and stored is not None and (type(stored) is not int or stored <= 0):
        incompatible('invalid stored keyword dimensions; rebuild required')
    sample = collection.get(limit=1, include=['embeddings'])
    ids, vectors = sample['ids'], sample['embeddings']
    if ids:
        if vectors is None or len(vectors) != len(ids) or not len(vectors[0]):
            incompatible('keyword dimensions unavailable; rebuild required')
        actual = len(vectors[0])
        if stored is not unknown and stored is not None and stored != actual:
            incompatible('stored keyword dimensions inconsistent; rebuild required')
    elif stored is not unknown and stored is not None:
        actual = stored
    elif stored is None and collection.count() == 0:
        actual = None  # Chroma에서 차원 미바인딩이 확인된 새/빈 컬렉션입니다.
    else:
        incompatible('keyword dimensions unavailable; rebuild required')
    if actual is not None and (actual != dimensions or declared is not None and declared != actual):
        incompatible(f'actual keyword dimensions={actual}, expected={dimensions}; rebuild required')
    return actual


def active_path(base):
    """포인터가 없는 기존 keywords/chroma.sqlite3도 그대로 읽습니다."""
    base = Path(base)
    pointer = base / 'active.json'
    if not pointer.exists():
        return base
    generation = json.loads(pointer.read_text(encoding='utf-8'))['generation']
    if not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{32}', generation):
        raise ValueError('invalid_keyword_generation')
    folder = base / 'generations' / generation
    status = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
    if status.get('status') != 'ready' or not (folder / 'chroma/chroma.sqlite3').is_file():
        raise ValueError('keyword_generation_not_ready')
    return folder / 'chroma'


def verify(collection, counts, dimensions):
    if collection.count() != len(counts):
        raise ValueError('keyword_verification_failed')
    seen = set()
    for offset in range(0, len(counts), 500):
        page = collection.get(limit=500, offset=offset, include=['documents', 'metadatas', 'embeddings'])
        ids = page['ids']
        if any(len(page[k]) != len(ids) for k in ('documents', 'metadatas', 'embeddings')):
            raise ValueError('keyword_verification_failed')
        for key, document, metadata, vector in zip(ids, page['documents'], page['metadatas'], page['embeddings']):
            # 일부 Chroma 버전은 같은 DB의 배열 메타데이터 필드를 빈 목록으로 반환합니다.
            # 필수 필드의 값은 엄격히 비교하고, 부가 필드는 비어 있는 경우만 허용합니다.
            expected = {'keyword': key, 'article_count': counts.get(key)}
            metadata_matches = (all(metadata.get(k) == v for k, v in expected.items())
                and all(v is None or v == [] for k, v in metadata.items() if k not in expected))
            if (key in seen or key not in counts or document != key
                    or not metadata_matches):
                error = ValueError('keyword_verification_failed')
                error.add_note(f'duplicate={key in seen}; unknown_id={key not in counts}; '
                               f'document_mismatch={document != key}; '
                               f'keyword_mismatch={metadata.get("keyword") != key}; '
                               f'count_mismatch={metadata.get("article_count") != counts.get(key)}; '
                               f'extra_metadata={sorted(set(metadata) - {"keyword", "article_count"})}')
                raise error
            if dimensions is None:
                dimensions = len(vector)  # 명시 주입 호환 클라이언트는 자신의 벡터 차원을 사용합니다.
            if (not dimensions or len(vector) != dimensions or not all(math.isfinite(float(v)) for v in vector)
                    or not any(float(v) for v in vector)):
                raise ValueError('invalid_keyword_embedding')
            seen.add(key)
    if seen != set(counts):
        raise ValueError('keyword_verification_failed')


def rebuild_in_client(client, counts, embed, *, model, dimensions):
    """빌린 구형 client에서도 임베딩 실패 전에 원래 컬렉션을 삭제하지 않습니다."""
    from backend.app.scheduler.jobs.news_embed import sync_keywords
    suffix = uuid4().hex
    temporary, backup = f'news_keyword_build_{suffix}', f'news_keyword_previous_{suffix}'
    original = None
    staged = client.create_collection(temporary, embedding_function=None,
        metadata={'embedding_model': model, 'dimensions': dimensions, 'hnsw:space': 'cosine'})
    try:
        result = sync_keywords(counts, staged, embed)
        verify(staged, counts, dimensions)
        if COLLECTION in [c.name for c in client.list_collections()]:
            original = client.get_collection(COLLECTION, embedding_function=None)
            original.modify(name=backup)
        staged.modify(name=COLLECTION)
        # 원본은 backup 이름으로 보존합니다. 소유권은 계속 호출자에게 있습니다.
        return staged, result
    except BaseException as error:
        try:
            names = {c.name for c in client.list_collections()}
            if COLLECTION in names:
                current = client.get_collection(COLLECTION, embedding_function=None)
                if current.id == staged.id:
                    client.delete_collection(COLLECTION)
            if original is not None and backup in {c.name for c in client.list_collections()}:
                original.modify(name=COLLECTION)
            if temporary in {c.name for c in client.list_collections()}:
                client.delete_collection(temporary)
        except BaseException as cleanup:
            error.add_note(f'keyword_collection_restore_failed:{type(cleanup).__name__}')
        raise


def rebuild(base, counts, embed, *, model, dimensions):
    """호출자는 base.lock을 보유합니다. 기존 저장소는 열거나 변경하지 않습니다."""
    from backend.app.services.rag.store import persistent_client
    from backend.app.scheduler.jobs.news_embed import sync_keywords
    base = Path(base)
    pointer = base / 'active.json'
    previous = json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else None
    generation = uuid4().hex
    folder = base / 'generations' / generation
    status = {'generation': generation, 'status': 'building', 'embedding_model': model,
              'dimensions': dimensions, 'distinct_keywords': len(counts)}
    atomic_json(folder / 'status.json', status)
    try:
        with closing_owned(persistent_client(folder / 'chroma'), name='keyword_store') as client:
            col = client.create_collection(COLLECTION, embedding_function=None,
                metadata={'embedding_model': model, 'dimensions': dimensions, 'hnsw:space': 'cosine'})
            result = sync_keywords(counts, col, embed)
            verify(col, counts, dimensions)
        # Windows에서도 열린 저장소를 이동하지 않습니다. 종료 실패도 게시 전에 중단합니다.
        status.update(result, status='ready')
        atomic_json(folder / 'status.json', status)
        atomic_json(pointer, {'generation': generation})
        return result
    except BaseException as error:
        try:
            if pointer.exists() and json.loads(pointer.read_text(encoding='utf-8')).get('generation') == generation:
                if previous is None:
                    pointer.unlink()
                else:
                    atomic_json(pointer, previous)
        except BaseException as rollback:
            error.add_note(f'keyword_pointer_rollback_failed:{type(rollback).__name__}')
        status.update(status='failed', error_code=type(error).__name__)
        try:
            atomic_json(folder / 'status.json', status)
        except BaseException as cleanup:
            error.add_note(f'keyword_status_write_failed:{type(cleanup).__name__}')
        raise
