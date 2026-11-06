"""기사 작성자·기고자와 KOTRA 담당자 정보를 표시 없이 삭제합니다.

기업·기관·행사 연락처 및 공공 핫라인은 기사 내용으로 보존합니다.
"""
import re

EMAIL = re.compile(r"[\w.+%*-]+[ \t]*@+[ \t\]]*[\w.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w])(?:\+?\(?\d{1,4}\)?[ .-]*){2,7}\d{2,9}(?:[ \t]*\(?(?:내선|Ext\.?|#)[ \t]*\d+\)?)?", re.I)
AUTHOR = re.compile(r"^([ \t*※ㅇ·-]*(?:공동[ \t]*)?(?:작성자|글쓴이|기고자|취재|글)[ \t]*[:：][ \t]*)(.+)$")
OFFICE = re.compile(r"KOTRA|코트라|무역관", re.I)
CONTACT = re.compile(r"문의|담\s*당|연락|이메일|메일|전화|팩스|\b(?:E-?mail|Tel|Fax)\b", re.I)
ROLE = r"(?:스페셜리스트|부관장|관장|과장|차장|대리|사원|주임|팀장|부장|위원|관세사|변호사|기자|특파원|PM)"
NAME_ROLE = re.compile(r"(?<![가-힣])([가-힣]{2,4})([ \t]+" + ROLE + r")\b")



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


def _phone_is_contact(match, value):
    text = match.group()
    groups = re.findall(r'\d+', text)
    prefix = value[max(0, match.start()-24):match.start()]
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
        r'((?:담\s*당\s*자|문의)[ \t]*[:：][ \t]*)([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,4})(?=[ \t]*(?:$|\())',
    ]
    for pattern in patterns:
        spans += [m.span(2) for m in re.finditer(pattern, line)]
    if writer and len(writer.strip()) >= 2:
        spans += [m.span() for m in re.finditer(r'(?<![\w])' + re.escape(writer.strip()) + r'(?![\w])', line)]
    return spans


def _clean_deleted_line(line):
    previous = None
    while previous != line:
        previous = line
        line = re.sub(r'\([ \t,，/;:：·-]*\)|（[ \t,，/;:：·-]*）|\[[ \t,，/;:：·-]*\]', '', line)
    line = re.sub(r'(?<![가-힣])(?:에게|께)(?=[ \t])', '', line)
    line = re.sub(r'[ \t]{2,}', ' ', line).strip()
    return re.sub(r'^[,，/;:：· \t]+|[,，/;:：· \t]+$', '', line)


def mask_article_contacts(body, *, title='', writer=''):
    """호출 이름은 유지하되 임시 표시 없이 원문의 대상 구간·행을 직접 삭제합니다."""
    lines = body.split('\n')
    out = []
    contributor=bool(re.match(r'^\[기고\]',title))
    # 기고문 첫머리의 짧은 서명/소속 블록. 본문 문장·소제목은 그대로 둡니다.
    contributor_indices=set()
    if contributor:
        for i,line in enumerate(lines[:12]):
            if not line.strip():continue
            if len(line)>=220 or re.search(r'[다요]\.(?:$|[ \t])',line):
                if i<2 and line.startswith('[편집자 주]'):continue
                break
            if re.search(r'서론|개요|요약문|들어가|일반 개황|외국계 기업의 러시아 시장 리스크|필요 -$|전망 -$|\?',line):continue
            has_identity=bool(re.search(r'(?:HR 총괄|HR 이사|박사과정|공인중개사|고문|사무국장|박사|변리사|세무사|상무|전무|책임|실장|통신원|전문가|법무사|지회장|지원장|대표|변호사|관세사|교수|컨설턴트|회계사|법인장|매니저|팀장|센터장|소장|부장|과장|이사|사장|연구원|위원|CEO|Manager|Director|Consultant|Specialist|Economist|Recruitment|Solictor|Solicitor|Engineer|Attorney|Founder)(?![가-힣])',line,re.I))
            short_name=i==0 and bool(re.match(r'^(?:[가-힣]{2,4}(?:$|[ ,(])|[가-힣] [가-힣]{2,5} [가-힣]{2,4} \()',line.strip()))
            english_name=i==0 and bool(re.match(r'^(?:Dr\. )?[A-Z][\w.-]+(?:[ \t]+[A-Z][\w.-]+){1,4}(?:$|[ ,(/])',line))
            continuation=(i-1 in contributor_indices) and (re.match(r'^[ \t]*(?:연락처|이메일|전화|E-?mail|Tel)[ \t:：]',line,re.I) or EMAIL.search(line))
            if has_identity or short_name or english_name or continuation:
                contributor_indices.add(i)
            elif i>2:break
    for i, line in enumerate(lines):
        if AUTHOR.match(line) or i in contributor_indices:
            continue
        nearby='\n'.join(v for v in lines[max(0,i-2):min(len(lines),i+3)] if not re.match(r'^[ \t\[<(]*(?:자료|출처)',v) and not re.search(r'공식[ \t]*의견|응대하지 말|자칭|교신을 중단',v))
        if re.search(r'이메일 사칭 예시|사칭 이메일',nearby):
            out.append(line);continue
        staff=bool(OFFICE.search(line) and (CONTACT.search(line) or EMAIL.search(line)) and not re.search(r'무역관과 인터뷰|무역관이 인터뷰',line))
        separate=bool(re.match(r'^[ \tㅇ·*-]*(?:문의|담\s*당\s*자|연락처|이메일|메일|전화|팩스|E-?mail|Tel|Fax)[ \t:：]',line,re.I) and OFFICE.search(nearby))
        if staff or separate:
            spans = _staff_spans(line, writer)
        else:
            spans = [m.span() for m in EMAIL.finditer(line)
                     if re.search(r'@+[ \t]*kotra\.or\.kr$', m.group(), re.I)]
        if not spans:
            out.append(line)
            continue
        contact_row = CONTACT_ROW.match(line)
        if contact_row:
            # 원문 좌표 그대로 삭제한 뒤 남는 날짜·수치·설명 유무를 확인합니다.
            payload_spans = [(max(start, contact_row.end()), end) for start, end in spans if end > contact_row.end()]
            payload = _delete_spans(line, payload_spans)[contact_row.end():]
            if not re.search(r'\d|https?://|(?:입니다|합니다|하세요|사업|기간|동안|까지|예정|가능)', payload):
                continue
        cleaned = _clean_deleted_line(_delete_spans(line, spans))
        if cleaned and re.search(r'[\w가-힣]', cleaned):
            out.append(cleaned)
    return '\n'.join(out)


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
