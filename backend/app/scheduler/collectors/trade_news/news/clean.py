"""본문 정제 초안: 원문을 보존하고 모든 변경을 행 단위로 기록합니다."""
import re
import hashlib
VERSION = 'clean-v4-20261020'
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
PHONE = re.compile(r'(?<!\d)(?:\+82[- .]?(?:10|2|[3-6]\d)|0(?:10|2|[3-6]\d|70))[- .]\d{3,4}[- .]\d{4}(?!\d)')
BYLINE = re.compile(r'(?:\[[가-힣A-Za-z =·,]{2,50}(?:기자|특파원)\]|(?:[가-힣]{2,12}=)?[가-힣]{2,5}(?:/(?:[가-힣]{2,12}=)?[가-힣]{2,5})*\s+(?:한경닷컴\s+)?(?:(?:객원|선임|수석)\s*)?(?:기자|특파원))')
# 숫자·통화·증감 기호는 보존하고 이모지 그림 및 결합 표지만 제거합니다.
EMOJI = re.compile(r'[\U0001F000-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\uFE0F\u200D\u20E3\U000E0020-\U000E007F]')
UI = {'영상 모듈 닫기','사진 확대','글자크기 설정','구독하기','기사 원문 보기','공유하기','인쇄','댓글','글자 크기','공유하기 · 인쇄'}
REASONS = {'ui':'독립된 화면 조작·구독 버튼 문구', 'byline':'독립된 기자 서명·연락처: 본문 사실 설명이 아님', 'contact':'개인 연락처 마스킹', 'notice':'기사 외부의 AI 요약 이용 안내', 'related':'명시적으로 표시된 다른 기사 안내', 'whitespace':'불필요한 앞뒤 공백·빈 줄 정리', 'inline_byline':'문장 끝에 붙은 짧은 기자 서명·연락처', 'premium_notice':'본문 앞의 유료 플랫폼 게재·구독 홍보 안내 (실제 접근 제한은 별도 검사)', 'summary_heading':'기사 외부의 핵심요약 제목·장식 이모지', 'emoji':'장식 이모지 제거, 문장·수치 보존'}


def clean_body(raw):
    changes, kept, warnings = [], [], []
    for number, original in enumerate(raw.splitlines(),1):
        line = original.strip(); result = line; kind = None
        no_contact = EMAIL.sub('', PHONE.sub('', line)).strip()
        if re.fullmatch(r'핵심\s*요약\s*쏙', EMOJI.sub('', line).strip()):
            result,kind = '', 'summary_heading'
        elif line in UI: result,kind = '', 'ui'
        elif BYLINE.fullmatch(no_contact): result,kind = '', 'byline'
        elif line.startswith('※ AI 요약은 OpenAI의 최신 기술을 활용해'):
            result,kind = '', 'notice'
        elif re.match(r'^(?:관련 기사|추천 기사)\s*[:：]',line):
            result,kind = '', 'related'
        else:
            # 문장부호 뒤에 서명이 독립적으로 끝나는 경우만 분리합니다.
            m = re.search(r'(?<=[.!?。])\s+((?:[가-힣]{2,12}=)?[가-힣]{2,5}\s+(?:한경닷컴\s+)?(?:기자|특파원)(?:\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})?)$',line)
            if m: result,kind = line[:m.start()].rstrip(),'inline_byline'
            masked = EMAIL.sub('[이메일 삭제]',PHONE.sub('[전화번호 삭제]',result))
            if masked != result:
                result = masked; kind = 'contact' if kind is None else kind
        # 확인된 게재·구독 홍보 안내 두 문장만 제거합니다. 본문이나 실제 장벽은 추정 삭제하지 않습니다.
        promo = re.match(r"^이 기사는 \d{1,2}월 \d{1,2}일[^\n]{0,180}?한국경제신문의 투자 정보 유료 플랫폼인 '한경 프리미엄9'[^\n]{0,100}?에 게재됐습니다\.\s*한경 프리미엄9을 구독하시면 더 많은 단독 기사를 실시간으로 볼 수 있습니다\.", result)
        # 저장 HTML에서 확인한 별도 홍보 형식도 두 문장이 모두 일치할 때만 제거합니다.
        # "구독해야 읽을 수 있음" 같은 실제 접근 제한 문구는 그대로 둡니다.
        if not promo:
            promo = re.match(r"^한국경제신문의 투자 전문 유료 플랫폼\s*'한경 프리미엄9'\s*\(www\.hankyung\.com/premium9\)\s*에 실린 기사입니다\.\s*'한경 프리미엄9'\s*을 구독하시면 더 많은 주식 투자 기사를 찾아볼 수 있습니다\.", result)
        if promo:
            result = result[promo.end():].strip()
            kind = 'premium_notice'
        if '핵심요약 쏙' in line:
            warnings.append('언론사 AI 요약 본문은 고유 정보 손실을 피하려고 보존했습니다.')
        without_emoji = EMOJI.sub('', result).strip()
        emoji_removed = without_emoji != result
        result = without_emoji
        if emoji_removed and kind is None:
            kind = 'emoji'
        if result != original:
            kind = kind or 'whitespace'
            changes.append({'line':number,'kind':kind,'reason':REASONS[kind] + ('; 장식 이모지도 제거' if emoji_removed and kind != 'emoji' else ''),'before':original,'after':result})
        if result: kept.append(result)
    # 원래 문단 순서는 유지하고 읽기 쉬운 빈 줄만 추가합니다.
    # 검수 참고 사항은 품질 실패 사유와 분리합니다. 요약 내용은 보존합니다.
    removed = [{**change, 'start_line':change['line'], 'end_line':change['line']} for change in changes]
    return {'body_masked':'\n\n'.join(kept), 'removed':removed,
            'warnings':[], 'review_notes':sorted(set(warnings)),
            'cleaning_version':VERSION, 'raw_sha256':hashlib.sha256(raw.encode()).hexdigest()}



def quality(title, cleaned):
    """짧다는 이유만으로 제외하지 않고 제목 반복·본문 누락 등을 구분합니다."""
    body = cleaned['body_masked']
    normalized = lambda s: re.sub(r'\s+', '', s)
    reasons = []
    if not body.strip():
        reasons.append('empty_body')
    elif normalized(body) == normalized(title):
        reasons.append('headline_only')
    if re.search(r'구독(?:하시면|해야)|로그인\s*후.*(?:읽|확인)|기사\s*전문.*구독|본문\s*일부만', body):
        reasons.append('access_limited')
    reasons.extend(cleaned['warnings'])
    return {'usable_body': not reasons, 'reasons': reasons,
            'short_body': len(body) < 100, 'length': len(body)}
