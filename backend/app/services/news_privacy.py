"""기사 작성자·기고자와 KOTRA 담당자 정보만 가립니다.

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


def contacts(value):
    value = EMAIL.sub('[이메일]', value)
    def phone(m):
        s=m.group();digits=re.sub(r'\D','',s)
        groups=re.findall(r'\d+',s)
        prefix=value[max(0,m.start()-24):m.start()]
        labelled=bool(re.search(r'(?:전화|팩스|Tel|Fax|Phone|연락처)[^\n]{0,12}$',prefix,re.I))
        domestic=s.lstrip('(').startswith('0') and len(groups)>=2
        if '+' not in s and not labelled and not domestic:return s
        if '+' not in s and (all(len(g)==4 for g in groups) or re.fullmatch(r'20\d{2}[ .-]\d{1,2}[ .-]\d{1,2}',s.strip())):return s
        return '[전화번호]' if len(digits)>=7 else s
    return PHONE.sub(phone,value)


def author_line(payload):
    # 명시적 작성자 표기의 이름 목록만 처리하고 앞의 소속은 유지합니다.
    parts=[]
    for part in re.split(r'([,，])',payload):
        if part in (',','，'):parts.append(part);continue
        m=re.match(r'(.*?(?:무역관|IP-DESK)[ \t]*)(.+)',part)
        prefix,person=(m.group(1),m.group(2)) if m else ('',part)
        leading=re.match(r'\s*',person).group()
        role=re.search(r'[ \t]+(?:'+ROLE+r'|Senior Specialist|Specialist|Assistant Manager|Assistant|Manager|Senior|Director)\b',person)
        suffix=person[role.start():] if role else ''
        # 이름 뒤 괄호 안 소속·연락처까지 작성자 정보로 처리합니다.
        parts.append(prefix+leading+'[이름]'+contacts(suffix))
    return ''.join(parts)


def mask_article_contacts(body, *, title='', writer=''):
    lines=body.split('\n');out=[]
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
            continuation=(i-1 in contributor_indices) and (re.match(r'^[ \t]*(?:연락처|이메일|전화|E-?mail|Tel)[ \t:：]',line,re.I) or EMAIL.search(line) or line.strip() in ('[이메일]','[전화번호]'))
            if has_identity or short_name or english_name or continuation:
                contributor_indices.add(i)
            elif i>2:break
    for i,line in enumerate(lines):
        m=AUTHOR.match(line)
        if m:
            out.append(m.group(1)+author_line(m.group(2)));continue
        if i in contributor_indices:
            # 기고자 서명은 외부 기업 연락처 목록과 구별되는 작성자 정보입니다.
            out.append('[작성자 정보]');continue
        nearby='\n'.join(v for v in lines[max(0,i-2):min(len(lines),i+3)] if not re.match(r'^[ \t\[<(]*(?:자료|출처)',v) and not re.search(r'공식[ \t]*의견|응대하지 말|자칭|교신을 중단',v))
        if re.search(r'이메일 사칭 예시|사칭 이메일',nearby):
            out.append(line);continue
        staff=bool(OFFICE.search(line) and (CONTACT.search(line) or EMAIL.search(line)) and not re.search(r'무역관과 인터뷰|무역관이 인터뷰',line))
        separate=bool(re.match(r'^[ \tㅇ·*-]*(?:문의|담\s*당\s*자|연락처|이메일|메일|전화|팩스|E-?mail|Tel|Fax)[ \t:：]',line,re.I) and OFFICE.search(nearby))
        if staff or separate:
            line=contacts(line)
            line=NAME_ROLE.sub(r'[이름]\2',line)
            line=re.sub(r'(무역관[ \t]+)(?!Invest Korea)([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)*)(?=\(|[ \t]+(?:Senior|Specialist))',r'\1[이름]',line)
            line=re.sub(r'(무역관[ \t]+)(?!담당자|이메일|연락처)([가-힣]{2,4})(?=[ \t]+\[이메일\])',r'\1[이름]',line)
            line=re.sub(r'(무역관[ \t]+)(?!담당자|이메일|연락처)([가-힣]{2,4})(?=[ \t]*\()',r'\1[이름]',line)
            line=re.sub(r'((?:담\s*당\s*자|문의)[ \t]*[:：][ \t]*)([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,4})(?=[ \t]*(?:$|\())',r'\1[이름]',line)
            if writer and len(writer.strip())>=2:
                line=re.sub(r'(?<![\w])'+re.escape(writer.strip())+r'(?![\w])','[이름]',line)
        # 연락처가 별도 줄에 놓여도 KOTRA 도메인은 소속이 명확합니다.
        line=EMAIL.sub(lambda m:'[이메일]' if re.search(r'@+[ \t]*kotra\.or\.kr$',m.group(),re.I) else m.group(),line)
        out.append(line)
    return '\n'.join(out)
