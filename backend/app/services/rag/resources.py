"""소유 자원의 종료 실패가 원래 처리 오류·취소를 덮지 않게 정리합니다."""


def close_owned(resources, *, primary=None):
    """호출자는 직접 소유한 (이름, close 함수)만 전달합니다.

    모든 종료를 한 번씩 시도하고, 실패 종류를 예외에 남깁니다.
    원래 예외가 없으면 첫 종료 오류를 호출자에게 전파합니다.
    """
    errors = []
    for name, close in resources:
        try:
            close()
        except BaseException as error:
            errors.append((name, error))
    if not errors:
        return
    target = primary if primary is not None else errors[0][1]
    for name, error in errors:
        target.add_note(f'cleanup_failed:{name}:{type(error).__name__}')
    if primary is None:
        raise target
