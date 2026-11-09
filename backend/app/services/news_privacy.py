"""작성자·기고자 정보를 삭제하고 본문의 문의 담당자 정보는 보존합니다."""
import re
from dataclasses import dataclass
EMAIL = re.compile(r"[\w.+%*-]+[ \t]*@+[ \t\]]*[\w.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w])(?:\+?\(?\d{1,4}\)?[ .-]*){2,7}\d{2,9}(?:[ \t]*\(?(?:내선|Ext\.?|#)[ \t]*\d+\)?)?", re.I)
AUTHOR = re.compile(r"^([ \t*※ㅇ·-]*(?:공동[ \t]*)?(?:작성자|글쓴이|기고자|취재|글)[ \t]*[:：][ \t]*)(.+)$")
ROLE = r"(?:스페셜리스트|부관장|관장|과장|차장|대리|사원|주임|팀장|부장|위원|관세사|변호사|기자|특파원|PM)"
def _delete_spans(value, spans):
    """원문 좌표에서 겹치는 구간을 합친 뒤 뒤쪽부터 삭제합니다."""
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        value = value[:start] + value[end:]
    return value


def _protected_tokens(value):
    """URL·이메일은 내부 문장부호까지 하나의 토큰으로 취급합니다."""
    spans = [m.span() for m in EMAIL.finditer(value)]
    spans += [(m.start(), m.start() + len(m.group().rstrip('.,;，；。')))
              for m in re.finditer(r'https?://[^\s<>]+', value)]
    return spans


def _phone_is_contact(match, value):
    # 구조화된 토큰 내부 숫자는 전화번호 후보에서 제외합니다.
    protected = _protected_tokens(value)
    if any(start < match.end() and match.start() < end for start, end in protected):
        return False
    text = match.group()
    groups = re.findall(r'\d+', text)
    prefix = value[max(0, match.start()-24):match.start()]
    # HS 표기 뒤의 분절된 품목 코드는 국내 전화번호와 모양이 같을 수 있습니다.
    if re.search(r'(?:\bHS(?:[ \t]*(?:CODE|코드))?|품목[ \t]*코드)[ \t:：#-]*(?:[0-9 .-]+[ \t]*[,，/][ \t]*)*$', value[:match.start()], re.I):
        return False
    labelled = bool(re.search(r'(?:전화|팩스|Tel|Fax|Phone|연락처)[^\n]{0,12}$', prefix, re.I))
    domestic = text.lstrip('(').startswith('0') and len(groups) >= 2
    if '+' not in text and not labelled and not domestic:
        return False
    if '+' not in text and (all(len(g) == 4 for g in groups) or re.fullmatch(r'20\d{2}[ .-]\d{1,2}[ .-]\d{1,2}', text.strip())):
        return False
    return len(re.sub(r'\D', '', text)) >= 7


def _clean_deleted_line(line):
    previous = None
    while previous != line:
        previous = line
        line = re.sub(r'\([ \t,，/;:：·-]*\)|（[ \t,，/;:：·-]*）|\[[ \t,，/;:：·-]*\]', '', line)
    line = re.sub(r'(?<![가-힣])(?:에게|께)(?=[ \t])', '', line)
    line = re.sub(r'[ \t]{2,}', ' ', line).strip()
    return re.sub(r'^[,，/;:：· \t]+|[,，/;:：· \t]+$', '', line)


SIGNATURE_ROLE = r"(?:HR 총괄|HR 이사|박사과정|공인중개사|고문|사무국장|박사|변리사|세무사|상무|전무|책임|실장|통신원|전문가|법무사|지회장|지원장|대표|변호사|관세사|교수|컨설턴트|회계사|법인장|매니저|팀장|센터장|소장|부장|과장|이사|사장|연구원|위원|CEO|Manager|Director|Consultant|Specialist|Economist|Recruitment|Solicitor|Engineer|Attorney|Founder)"


def _contributor_signature(line, writer):
    """서명 전체가 이름·소속·직함·연락처 문법일 때만 인정합니다."""
    value = line.strip()
    # 연락처는 서명의 부속 필드로 분리합니다. 본문 숫자를 지우지는 않습니다.
    spans = [m.span() for m in EMAIL.finditer(value)]
    spans += [m.span() for m in PHONE.finditer(value) if _phone_is_contact(m, value)]
    identity = _clean_deleted_line(_delete_spans(value, spans))
    if writer.strip() and identity == writer.strip():
        return True
    name = re.escape(writer.strip()) if writer.strip() else r'[가-힣]{2,4}'
    # 알려진 작성자가 수집 담당자일 수도 있으므로 별도 기고자의 명시적 서명도 인식합니다.
    name = '(?:' + name + r'|[가-힣]{2,4}|(?:Dr\. )?[A-Z][\w.-]+(?:[ \t]+[A-Z][\w.-]+){1,4})'
    organization = r'(?:[A-Z][A-Z0-9]+\([^()]+\)|[\w]+(?:회사|법인|대학교|대학|연구소|사무소)|법무법인[ \t]+[\w]+)'
    return bool(re.fullmatch(name + r'[ \t,]+(?:' + organization + r'[ \t]+(?:[가-힣]+(?=법인장))?)?' + SIGNATURE_ROLE, identity) or
                re.fullmatch(organization + r'[ \t]+' + name + r'[ \t]+' + SIGNATURE_ROLE, identity))


def _signature_affiliation(line):
    value = line.strip()
    return bool(re.search(r'법무법인|법인|사무소|대학교|대학|연구소|회사', value) and
                not re.search(r'\d|[.!?。]', value) and
                re.fullmatch(r'[^\n]{1,90}' + SIGNATURE_ROLE, value))


# 명시적인 작성자 행 뒤에서만 사용하는 두 필드 형식입니다.
AUTHOR_AFFILIATION_ONLY = re.compile(
    r'(?!(?:.*(?:문의|담당자|연락처|행사 안내)))'
    r'(?:소속[ \t]*[:：][ \t]*[\w㈜().·& -]+|'
    r'(?:법무법인|주식회사|㈜|\(주\))[ \t]*[\w·& -]+)'
)
SIGNATURE_DEADLINE = re.compile(
    r'신청[ \t]*마감(?:일|기한)?[ \t]*[:：]?[ \t]*'
    r'(?:(?:\d{4}년[ \t]*)?\d{1,2}월[ \t]*\d{1,2}일|\d{4}[./-]\d{1,2}[./-]\d{1,2})'
    r'(?:[ \t]*\d{1,2}(?::\d{2}|시(?:[ \t]*\d{1,2}분)?))?(?:까지)?'
)


def _signature_deadline(line):
    for group in re.finditer(r'[(（]([^()（）]*)[)）]', line):
        deadline = SIGNATURE_DEADLINE.search(line, *group.span(1))
        if deadline:
            return deadline


AUTHOR_EMAIL_IN_PROSE = re.compile(
    r'^[ \t]*(?:작성자(?:에게|께)[ \t]+|(?=(?:이메일|메일|E-?mail)[ \t]*[:：]))'
    r'(?P<field>(?:(?:이메일|메일|E-?mail)[ \t]*[:：][ \t]*)?'
    + EMAIL.pattern + r'(?:으로|로)?)(?=[ \t]+(?:문의|연락))', re.I)


AUTHOR_PHONE_EXTENSION = re.compile(
    r'(?:연락처|전화(?:번호)?|Tel)[ \t:：]+' + PHONE.pattern + r'[ \t]*\(\d+\)', re.I
)


def _signature_contact(line):
    value = line.strip()
    return bool(EMAIL.fullmatch(value) or
                re.fullmatch(r'(?:이메일|연락처|전화(?:번호)?|E-?mail|Tel)[ \t:：]+(?:' + EMAIL.pattern + r'|' + PHONE.pattern + r'(?:~\d+)?)', value, re.I))


@dataclass(frozen=True)
class LinePlan:
    """원문 한 행의 삭제 좌표와 판정 근거. 분석 단계에서는 본문을 수정하지 않습니다."""
    spans: tuple = ()
    drop: bool = False
    reason: str = 'preserve'


def _author_prefix_end(line):
    """작성자 필드의 끝을 찾되 같은 행의 다음 문장은 남깁니다."""
    author = AUTHOR.match(line)
    if not author:
        return None
    inquiry = re.search(r'(?:문의(?:처)?|담당자)[ \t]*[:：]', line[author.end(1):])
    if inquiry:
        line = line[:author.end(1) + inquiry.start()]
    protected = _protected_tokens(line)
    depth = 0
    for i in range(author.end(1), len(line)):
        if line[i] in '(（[':
            depth += 1
        elif line[i] in ')）]':
            depth = max(0, depth - 1)
        elif not depth and line[i] in '.。;；' and (i + 1 == len(line) or line[i + 1].isspace()):
            if not any(start <= i < end for start, end in protected):
                # 영문 이름의 약자는 문장 경계로 취급하지 않습니다.
                if line[i] == '.' and re.search(r'\b(?:[A-Z]|Dr|Mr|Ms)$', line[:i]):
                    continue
                end = i + 1
                while end < len(line) and line[end].isspace():
                    end += 1
                return end
    return len(line)


def plan_article_contacts(body, *, title='', writer=''):
    """작성자 필드와 서명 문법으로 삭제 계획을 구성합니다. 원문 좌표를 유지합니다."""
    lines = body.split('\n')
    plans = []
    signature_open = bool(re.match(r'^\[기고\]', title))
    signature_started = False
    author_active = False
    for i, line in enumerate(lines):
        if not line.strip():
            plans.append(LinePlan())
            continue
        author_end = _author_prefix_end(line)
        if author_end is not None:
            author_active = author_end == len(line)
            if not author_active:
                signature_open = False
            deadline = _signature_deadline(line[:author_end])
            if deadline:
                end = deadline.end()
                # 후속 본문과 마감일 사이의 공백을 보존합니다.
                boundary = len(line[:author_end].rstrip()) if not author_active else author_end
                plans.append(LinePlan(((0, deadline.start()), (end, boundary)), reason='explicit-author'))
            else:
                plans.append(LinePlan(((0, author_end),), author_active, 'explicit-author'))
            continue
        contact = AUTHOR_EMAIL_IN_PROSE.match(line) if (
            author_active or line.lstrip().startswith(('작성자에게', '작성자께'))
        ) else None
        if contact and re.fullmatch(
                r'[ \t]+(?:문의|연락)[ \t]*(?:하세요|바랍니다|해 주세요|해 주시기 바랍니다)[.!。]?[ \t]*',
                line[contact.end():]):
            plans.append(LinePlan(((0, len(line)),), True, 'author-contact'))
            continue
        if author_active:
            if AUTHOR_AFFILIATION_ONLY.fullmatch(line.strip()):
                plans.append(LinePlan(reason='author-affiliation'))
                continue
            if contact:
                plans.append(LinePlan((contact.span('field'),), reason='author-email-in-prose'))
                author_active = signature_open = False
                continue
            if _signature_contact(line) or AUTHOR_PHONE_EXTENSION.fullmatch(line.strip()):
                plans.append(LinePlan(((0, len(line)),), True, 'author-contact'))
                continue
            author_active = False
        if signature_open:
            if line.lstrip().startswith('[편집자 주]') and not signature_started:
                plans.append(LinePlan())
                continue
            following = next((v for v in lines[i + 1:] if v.strip()), '')
            bare_name = bool(re.fullmatch(r'[가-힣]{2,4}', line.strip()) and _signature_affiliation(following))
            deadline = _signature_deadline(line)
            if deadline and _contributor_signature(_delete_spans(line, [deadline.span()]), writer):
                plans.append(LinePlan(((0, deadline.start()), (deadline.end(), len(line))), reason='contributor-signature'))
                signature_started = True
                continue
            if (_contributor_signature(line, writer) or bare_name or
                    signature_started and (_signature_contact(line) or _signature_affiliation(line))):
                plans.append(LinePlan(((0, len(line)),), True, reason='contributor-signature'))
                signature_started = True
                continue
            signature_open = False
        plans.append(LinePlan())
    return tuple(plans)


def mask_article_contacts(body, *, title='', writer=''):
    """분석이 끝난 원문 좌표를 직접 삭제합니다. 임시 표시는 생성하지 않습니다."""
    plans = plan_article_contacts(body, title=title, writer=writer)
    output = []
    for line, plan in zip(body.split('\n'), plans):
        if plan.drop:
            continue
        if not plan.spans:
            output.append(line)
        else:
            result = _delete_spans(line, plan.spans)
            if plan.reason != 'explicit-author':
                result = _clean_deleted_line(result)
            if result:
                output.append(result)
    return '\n'.join(output)


MARKERS = re.compile(r"\[(?:이름|이메일|전화번호|작성자 정보)\]")
CONTACT_ROW = re.compile(
    r"^[ \t*※ㅇ·-]*(?:(?:문의(?:처)?|담\s*당\s*자|연락처|이메일|메일|전화(?:번호)?|팩스|E-?mail|Tel|Fax)[ \t]*[:：]"
    r"|[^.!?。\n]*?(?:문의(?:처)?|담\s*당\s*자)[ \t]*[:：])", re.I
)


def remove_legacy_markers(body):
    """기존 저장 데이터의 표시를 정리합니다. 신규 기사 처리와는 별개입니다."""
    lines = []
    for original in body.split('\n'):
        if not MARKERS.search(original):
            lines.append(original)
            continue
        # 작성자 및 독립된 연락처 행은 빈 소속·직함만 남기지 않습니다.
        if AUTHOR.match(original):
            continue
        contact_row = CONTACT_ROW.match(original)
        if contact_row:
            payload = MARKERS.sub('', original[contact_row.end():])
            # 연락처와 함께 적힌 날짜·수치·설명 문장은 행째 삭제하지 않습니다.
            has_facts = bool(re.search(r'\d|https?://|(?:입니다|합니다|하세요|사업|기간|동안|까지|예정|가능)', payload))
            if not has_facts:
                continue
        line = re.sub(r'\[이름\][ \t]*(?:' + ROLE + r')\b', '', original)
        line = re.sub(r'(?:이메일|메일|전화번호|전화|팩스|E-?mail|Tel|Fax)[ \t]*[:：][ \t]*(?=\[(?:이메일|전화번호)\])', '', line, flags=re.I)
        line = MARKERS.sub('', line)
        # 마스킹 정보로만 구성됐던 괄호·구분기호를 정리합니다.
        previous = None
        while previous != line:
            previous = line
            line = re.sub(r'\([ \t,，/;:：·-]*\)|（[ \t,，/;:：·-]*）|\[[ \t,，/;:：·-]*\]', '', line)
        line = re.sub(r'(?<![가-힣])(?:에게|께)(?=[ \t])', '', line)
        line = re.sub(r'[ \t]{2,}', ' ', line).strip()
        line = re.sub(r'^[,，/;:：· \t]+|[,，/;:：· \t]+$', '', line)
        if line and re.search(r'[\w가-힣]', line):
            lines.append(line)
    return '\n'.join(lines)
