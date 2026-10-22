"""프로젝트 설계의 MySQL article/article_tag에 신규 데이터를 적재합니다.

전용 MySQL Connector/Python 연결과 튜플 커서를 사용합니다. 연결·DDL·스케줄 실행은 하지 않습니다.
기존 본문은 자동으로 덮어쓰지 않습니다.
"""
from datetime import date


class ArticleConflict(ValueError):
    """같은 원천 ID에 다른 내용이 있어 기존 데이터를 덮어쓸 수 없습니다."""


class MySQLArticleStore:
    def __init__(self, connection, source_mapping, *, include_tags=False):
        self.include_tags = include_tags
        self.db = connection
        self.source_mapping = dict(source_mapping)

    def save(self, prepared):
        if not prepared['eligible']:
            raise ValueError('품질·주제 검증을 통과한 기사만 적재합니다')
        source = self.source_mapping[prepared['source_code']]
        if not isinstance(source, str) or not source.strip() or len(source)>40:
            raise ValueError('SOURCE 코드 형식 오류')
        for field, maximum in [('external_id',100),('title',600),('source_url',2048)]:
            value = prepared[field]
            if not isinstance(value,str) or not value.strip() or len(value)>maximum:
                raise ValueError(f'{field}: 프로젝트 필드 길이·형식 오류')
        if not prepared['external_id'].isascii():
            raise ValueError('external_id는 ASCII 문자열이어야 합니다')
        if not isinstance(prepared['body_masked'],str) or not prepared['body_masked'].strip():
            raise ValueError('서비스 본문이 비어 있습니다')
        category = prepared.get('category')
        if category is not None and (not isinstance(category,str) or len(category)>120):
            raise ValueError('category 형식 오류')
        values = (prepared['title'], date.fromisoformat(prepared['published_date']),
                  prepared['source_url'], prepared['body_masked'], category)
        cursor = self.db.cursor(buffered=True)
        started = False
        try:
            # START TRANSACTION이 호출자의 기존 작업을 커밋하지 않도록 먼저 검사합니다.
            if self.db.in_transaction:
                raise ValueError('이미 트랜잭션이 진행 중인 연결은 사용할 수 없습니다')
            cursor.execute("SELECT TABLE_NAME,ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('article','article_tag','service_option')")
            engines = dict(cursor.fetchall())
            # 기본 적재 범위는 article입니다. 태그 테이블은 선택 적재 때만 요구합니다.
            required = {'article','service_option'} | ({'article_tag'} if self.include_tags else set())
            if not required <= engines.keys() or any(engines[k].upper()!='INNODB' for k in required):
                raise ValueError('적재 대상의 InnoDB 테이블이 필요합니다')
            self.db.start_transaction()
            started = True
            # 동일 SOURCE 적재를 직렬화해 중복 삽입 경쟁을 줄입니다.
            cursor.execute("SELECT option_code FROM service_option WHERE option_code=%s AND option_type='SOURCE' AND enabled=1 FOR UPDATE", (source,))
            if cursor.fetchone() is None:
                raise ValueError('활성 SOURCE 코드가 프로젝트 DB에 없습니다')
            cursor.execute('SELECT article_id,title,published_date,source_url,body_masked,category FROM article WHERE source_code=%s AND external_id=%s FOR UPDATE',
                           (source,prepared['external_id']))
            existing = cursor.fetchone()
            if existing:
                actual = (existing[1],date.fromisoformat(str(existing[2])),*existing[3:])
                if actual != values:
                    raise ArticleConflict('동일 원천 ID의 내용이 다릅니다. 기존 기사와 태그를 보존했습니다')
                article_id = existing[0]
            else:
                cursor.execute('INSERT INTO article(source_code,external_id,title,published_date,source_url,body_masked,category) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                               (source,prepared['external_id'],*values))
                article_id = cursor.lastrowid
            for tag in (prepared['tagging']['tags'] if self.include_tags else []):
                fields = ('country_code','hs_code','hs_ver','keyword_text')
                country, hs, version, keyword = row = tuple(tag.get(key) for key in fields)
                if sum(value is not None for value in (country,hs,keyword))!=1 or (hs is None)!=(version is None):
                    raise ValueError('태그는 국가·HS·키워드 중 하나여야 합니다')
                if keyword is not None and (not isinstance(keyword,str) or not keyword.strip() or len(keyword)>120):
                    raise ValueError('키워드 형식 오류')
                # 기존 수동 태그는 삭제하지 않습니다. 사전에 없는 코드는 DB 외래 키가 거부합니다.
                condition = 'country_code=%s' if country is not None else ('hs_code=%s AND hs_ver=%s' if hs is not None else 'keyword_text=%s')
                identity = (country,) if country is not None else ((hs,version) if hs is not None else (keyword,))
                cursor.execute('SELECT tag_id FROM article_tag WHERE article_id=%s AND '+condition, (article_id,*identity))
                if cursor.fetchone() is None:
                    cursor.execute('INSERT INTO article_tag(article_id,country_code,hs_code,hs_ver,keyword_text) VALUES(%s,%s,%s,%s,%s)', (article_id,*row))
            self.db.commit()
            return article_id
        except Exception:
            if started:
                self.db.rollback()
            raise
        finally:
            cursor.close()
