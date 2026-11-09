"""기사 작성자·기고자와 KOTRA 담당자 정보를 표시 없이 삭제합니다.

기업·기관·행사 연락처 및 공공 핫라인은 기사 내용으로 보존합니다.
"""
import re
from dataclasses import dataclass
from enum import Enum

EMAIL = re.compile(r"[\w.+%*-]+[ \t]*@+[ \t\]]*[\w.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w])(?:\+?\(?\d{1,4}\)?[ .-]*){2,7}\d{2,9}(?:[ \t]*\(?(?:내선|Ext\.?|#)[ \t]*\d+\)?)?", re.I)
AUTHOR = re.compile(r"^([ \t*※ㅇ·-]*(?:공동[ \t]*)?(?:작성자|글쓴이|기고자|취재|글)[ \t]*[:：][ \t]*)(.+)$")
OFFICE = re.compile(r"KOTRA|코트라|무역관", re.I)
CONTACT = re.compile(r"문의|담\s*당|연락|이메일|메일|전화|팩스|\b(?:E-?mail|Tel|Fax)\b", re.I)
ROLE = r"(?:스페셜리스트|부관장|관장|과장|차장|대리|사원|주임|팀장|부장|위원|관세사|변호사|기자|특파원|PM)"
PARTICLE = r"(?:에게서|에게|께서|께|으로부터|으로|은|는|이|가|을|를|의|와|과|도|만)"
NAME_ROLE = re.compile(r"(?<![가-힣])([가-힣]{2,4})([ \t]+" + ROLE + r")(?:님|씨)?(?:(?:" + PARTICLE + r")(?![\w])|(?![\w]))")



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


def _staff_spans(line, writer):
    spans = [m.span() for m in EMAIL.finditer(line)]
    spans += [m.span() for m in PHONE.finditer(line) if _phone_is_contact(m, line)]
    # 연락처 바로 앞의 필드명도 함께 제거합니다.
    for start, end in list(spans):
        label = re.search(r'(?:이메일|메일|전화번호|전화|팩스|E-?mail|Tel|Fax)[ \t]*[:：][ \t]*$', line[:start], re.I)
        if label:
            spans.append((label.start(), end))
    spans += [m.span() for m in NAME_ROLE.finditer(line)]
    patterns = [
        r'(무역관[ \t]+)(?!Invest Korea)([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)*)(?=\(|[ \t]+(?:Senior|Specialist))',
        r'(무역관[ \t]+)(?!담당자|이메일|연락처)([가-힣]{2,4})(?=[ \t]+(?:' + EMAIL.pattern + r'))',
        r'(무역관[ \t]+)(?!담당자|이메일|연락처)([가-힣]{2,4})(?=[ \t]*\()',
        r'((?:담\s*당\s*자)[ \t]*[:：][ \t]*)([가-힣]{2,4})(?=[ \t]*(?:$|\())',
        r'((?:담\s*당\s*자|문의)[ \t]*[:：][ \t]*)([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,4})(?=[ \t]*(?:$|\())',
    ]
    for pattern in patterns:
        spans += [m.span(2) for m in re.finditer(pattern, line)]
    if writer and len(writer.strip()) >= 2:
        spans += [m.span() for m in re.finditer(r'(?<![\w])' + re.escape(writer.strip()) + r'(?:님|씨)?(?:(?:에게서|에게|께서|께|으로부터|으로|은|는|이|가|을|를|의|와|과|도|만)(?![\w])|(?![\w]))', line)]
    return spans


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


def _signature_contact(line):
    value = line.strip()
    return bool(EMAIL.fullmatch(value) or
                re.fullmatch(r'(?:이메일|연락처|전화(?:번호)?|E-?mail|Tel)[ \t:：]+(?:' + EMAIL.pattern + r'|' + PHONE.pattern + r'(?:~\d+)?)', value, re.I))


EXTERNAL_OWNER = re.compile(
    r'주최사|주최측|제조사|공급사|판매사|운영사|전시[ \t]*주최|'
    r'외부[ \t]*(?:기업|기관|행사)|회사[ \t]*담당자|협회'
)
QUOTE_LABEL = re.compile(r'이메일 사칭 예시|사칭 이메일')


def _quoted_contact_spans(line):
    """예시 표기부터 그 구간 끝까지만 보호합니다. 이웃 행에는 전파하지 않습니다."""
    spans = []
    tokens = _protected_tokens(line)
    for marker in QUOTE_LABEL.finditer(line):
        end = len(line)
        for boundary in re.finditer(r'[;；]|[.。](?=\s)|(?:문의(?:처)?|담당자)[ \t]*[:：]', line[marker.end():]):
            pos = marker.end() + boundary.start()
            if not any(a <= pos < b for a, b in tokens):
                end = pos
                break
        spans.append((marker.start(), end))
    return spans


def _external_owner(line):
    # 외부 소속이 명시된 연락처는 주변 KOTRA 언급보다 우선합니다.
    return bool(EXTERNAL_OWNER.search(line)) and not bool(
        re.search(r'(?:문의(?:처)?|담당자)[ \t:：]*(?:KOTRA|코트라|[^:：\n]*무역관)', line, re.I))




def _contact_residue_only(payload):
    residue = _clean_deleted_line(payload)
    residue = re.sub(r'(?<![\w])(?:또는|및|혹은|and|or)(?![\w])', '', residue, flags=re.I)
    residue = re.sub(r'^(?:KOTRA|코트라)[ \t]*', '', residue, flags=re.I)
    residue = re.sub(r'^(?:[A-Za-z가-힣 .-]+[ \t]+)?무역관', '', residue).strip()
    residue = re.sub(r'^(?:' + ROLE + r'|Senior Specialist|Specialist|Assistant Manager|Manager)$', '', residue, flags=re.I)
    return not re.search(r'[\w가-힣]', residue)


def _contact_clauses(line):
    """소속이 바뀌는 경계만 분리합니다. URL과 같은 담당자의 후속 전화번호는 유지합니다."""
    if not OFFICE.search(line) or not (EXTERNAL_OWNER.search(line) or QUOTE_LABEL.search(line)):
        return [line]
    depth, start, parts = 0, 0, []
    separators = re.compile(r'[,，;；]|[ \t]+/[ \t]+|[ \t]+(?:또는|혹은|및)[ \t]+')
    protected = _protected_tokens(line)
    candidates = {m.start(): m for m in separators.finditer(line)
                  if not any(a <= m.start() < b for a, b in protected)}
    for i, char in enumerate(line):
        if char in '(（[':
            depth += 1
        elif char in ')）]':
            depth = max(0, depth - 1)
        if depth or i not in candidates:
            continue
        separator = candidates[i]
        rest = line[separator.end():]
        # 연락처 값 앞에 명시적인 소속이 있어야 새 담당자 구간입니다.
        prefix = re.split(r'[,，;；(（]|https?://|[\w.+%-]+@', rest, maxsplit=1)[0]
        if OFFICE.search(prefix) or EXTERNAL_OWNER.search(prefix):
            parts.append(line[start:i].strip())
            start = separator.end()
    parts.append(line[start:].strip())
    return parts


class Owner(Enum):
    UNKNOWN = 'unknown'
    OFFICE = 'office'
    EXTERNAL = 'external'


@dataclass(frozen=True)
class LinePlan:
    """원문 한 행의 삭제 좌표와 판정 근거. 분석 단계에서는 본문을 수정하지 않습니다."""
    spans: tuple = ()
    drop: bool = False
    owner: Owner = Owner.UNKNOWN
    reason: str = 'preserve'


def _author_prefix_end(line):
    """작성자 필드의 끝을 찾되 같은 행의 다음 문장은 남깁니다."""
    author = AUTHOR.match(line)
    if not author:
        return None
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


def _contact_only(line, writer):
    """블록의 소속을 다음 행에 전달해도 되는 개인정보 전용 행인지 확인합니다."""
    spans = _staff_spans(line, writer)
    field = CONTACT_ROW.match(line)
    if field:
        spans = [(max(start, field.end()), end) for start, end in spans if end > field.end()]
        residue = _delete_spans(line, spans)[field.end():]
    else:
        residue = _delete_spans(line, spans)
    return bool(spans) and _contact_residue_only(residue)


def _segment_plan(line, owner, writer):
    external = _external_owner(line)
    explicit_office = bool(OFFICE.search(line)) and not external
    if external:
        return LinePlan(owner=Owner.EXTERNAL, reason='external-owner')
    office_context = explicit_office or owner is Owner.OFFICE
    is_identity = bool(NAME_ROLE.fullmatch(line.strip())) or _contact_only(line, writer)
    if office_context and (CONTACT.search(line) or EMAIL.search(line) or is_identity):
        spans = _staff_spans(line, writer)
    else:
        spans = [m.span() for m in EMAIL.finditer(line)
                 if re.search(r'@+[ \t]*kotra\.or\.kr$', m.group(), re.I)]
    quoted = _quoted_contact_spans(line)
    if quoted:
        spans = [(a, b) for a, b in spans if not any(x < b and a < y for x, y in quoted)]
        return LinePlan(tuple(spans), False, Owner.UNKNOWN, 'quoted-contact-boundary')
    # 일반 문장은 블록을 끝냅니다. 빈 줄은 상위 파서가 그대로 통과시킵니다.
    heading = explicit_office and not re.search(r'[.!?。]|(?:합니다|입니다)$', line)
    next_owner = Owner.OFFICE if office_context and (heading or _contact_only(line, writer)) else Owner.UNKNOWN
    if not spans:
        return LinePlan(owner=next_owner)
    field = CONTACT_ROW.match(line)
    drop = _contact_only(line, writer)
    if field and not drop:
        payload_spans = [(max(start, field.end()), end) for start, end in spans if end > field.end()]
        drop = _contact_residue_only(_delete_spans(line, payload_spans)[field.end():])
    return LinePlan(tuple(spans), drop, next_owner, 'office-contact')


def plan_article_contacts(body, *, title='', writer=''):
    """소속 상태와 서명 문법으로 삭제 계획을 구성합니다. 원문 좌표를 유지합니다."""
    lines = body.split('\n')
    plans = []
    owner = Owner.UNKNOWN
    signature_open = bool(re.match(r'^\[기고\]', title))
    signature_started = False
    author_active = False
    quote_email_pending = False
    for i, line in enumerate(lines):
        if not line.strip():
            plans.append(LinePlan(owner=owner))
            continue
        if quote_email_pending:
            quote_email_pending = False
            # 명시적 예시 표제 바로 다음 이메일 전용 행 한 개만 보호합니다.
            if EMAIL.fullmatch(line.strip()):
                plans.append(LinePlan(reason='quoted-email-continuation'))
                continue
        if re.fullmatch(r'[ \t]*(?:이메일 사칭 예시|사칭 이메일)[ \t]*[:：][ \t]*', line):
            quote_email_pending = True
            owner = Owner.UNKNOWN
            author_active = False
            signature_open = False
            plans.append(LinePlan(reason='quoted-email-heading'))
            continue
        author_end = _author_prefix_end(line)
        if author_end is not None:
            author_active = author_end == len(line)
            # 작성자 여부와 소속은 별개입니다. 작성자 행을 삭제해도 무역관 블록을 끊지 않습니다.
            if not author_active:
                owner = Owner.UNKNOWN  # 같은 행의 후속 본문은 블록 경계입니다.
                signature_open = False
                signature_started = False
            elif OFFICE.search(line[:author_end]) and not _external_owner(line[:author_end]):
                owner = Owner.OFFICE
            plans.append(LinePlan(((0, author_end),), author_active, owner, 'explicit-author'))
            continue
        if author_active:
            if (not _external_owner(line) and not OFFICE.search(line) and not QUOTE_LABEL.search(line)
                    and (_signature_contact(line) or _contact_only(line, writer) or _signature_affiliation(line))):
                plans.append(LinePlan(((0, len(line)),), True, owner, 'author-contact'))
                continue
            author_active = False
        if signature_open:
            if line.lstrip().startswith('[편집자 주]') and not signature_started:
                plans.append(LinePlan())
                continue
            following = next((v for v in lines[i + 1:] if v.strip()), '')
            bare_name = bool(re.fullmatch(r'[가-힣]{2,4}', line.strip()) and _signature_affiliation(following))
            if (_contributor_signature(line, writer) or bare_name or
                    signature_started and (_signature_contact(line) or _signature_affiliation(line))):
                plans.append(LinePlan(((0, len(line)),), True, reason='contributor-signature'))
                signature_started = True
                continue
            signature_open = False
        if re.search(r'무역관과 인터뷰|무역관이 인터뷰', line):
            plans.append(LinePlan(reason='quoted-contact'))
            owner = Owner.UNKNOWN
            continue
        clauses = _contact_clauses(line)
        if len(clauses) == 1:
            plan = _segment_plan(line, owner, writer)
        else:
            # 각 소속 구간을 독립 판정하되 원문 구분자와 외부 구간은 그대로 보존합니다.
            positions, cursor, parts = [], 0, []
            for clause in clauses:
                start = line.index(clause, cursor)
                positions.append((start, start + len(clause)))
                cursor = start + len(clause)
                parts.append(_segment_plan(clause, Owner.UNKNOWN, writer))
            spans = []
            survivors = [j for j, part in enumerate(parts) if not part.drop]
            for j, (part, (start, end)) in enumerate(zip(parts, positions)):
                if part.drop:
                    if any(k > j for k in survivors):
                        end = positions[j + 1][0]
                    elif j:
                        start = positions[j - 1][1]
                    spans.append((start, end))
                else:
                    spans.extend((start + a, start + b) for a, b in part.spans)
            plan = LinePlan(tuple(spans), not survivors, parts[-1].owner, 'mixed-owners')
        plans.append(plan)
        owner = plan.owner
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
            result = _clean_deleted_line(_delete_spans(line, plan.spans))
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
