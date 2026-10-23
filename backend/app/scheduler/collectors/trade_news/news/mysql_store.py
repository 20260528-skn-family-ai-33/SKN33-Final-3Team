"""팀 공통 SQLAlchemy 연결로 전처리 기사만 적재합니다. 연결 생성·DDL은 수행하지 않습니다."""
from datetime import date
from sqlalchemy import Integer, column, table, select, text, inspect
from news.article_schema import article_values

SOURCE = table('service_option',column('option_code'),column('option_type'),column('enabled'))
TAG = table('article_tag',column('tag_id',Integer),column('article_id',Integer),column('country_code'),
            column('hs_code'),column('hs_ver'),column('keyword_text'))

class ArticleConflict(ValueError):
    """같은 출처·ID의 저장 내용이 다르면 덮어쓰지 않습니다."""


def require_tables(connection, include_tags):
    required={'article','service_option'} | ({'article_tag'} if include_tags else set())
    if connection.dialect.name == 'mysql':
        engines=dict(connection.execute(text("SELECT TABLE_NAME,ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('article','article_tag','service_option')")).all())
        if not required <= engines.keys() or any(str(engines[k]).upper()!='INNODB' for k in required):
            raise ValueError('적재 대상의 InnoDB 테이블이 필요합니다')
    elif connection.dialect.name == 'sqlite':
        # 공통 make_engine(url)의 로컬 시험 경로. 실제 운영 URL은 mysql+pymysql입니다.
        if not required <= set(inspect(connection).get_table_names()):
            raise ValueError('적재 대상 테이블이 없습니다')
    else:
        raise ValueError('지원하지 않는 DB 연결입니다')


class MySQLArticleStore:
    def __init__(self, connection, source_mapping, *, include_tags=False):
        self.db=connection
        self.source_mapping=dict(source_mapping)
        self.include_tags=include_tags

    def save(self, prepared, *, return_result=False):
        values=article_values(prepared)
        source=self.source_mapping[prepared['source_code']]
        if not isinstance(source,str) or not source.strip() or len(source)>40:
            raise ValueError('SOURCE 코드 형식 오류')
        if self.db.in_transaction():
            raise ValueError('이미 트랜잭션이 진행 중인 연결은 사용할 수 없습니다')
        # 메타데이터 SELECT보다 먼저 시작합니다. 조회의 autobegin과 중복 시작하지 않습니다.
        # 예외가 발생하면 정보 조회/INSERT/태그 전체가 이 문맥에서 rollback됩니다.
        with self.db.begin():
            from backend.app.services.news_schema import reflect, article_insert_rows
            require_tables(self.db,self.include_tags)
            # 실제 컬럼을 읽어 구형 ERD와 관리자 ERD를 함께 지원합니다. DDL은 실행하지 않습니다.
            article_table = reflect(self.db, 'article')
            registered=self.db.execute(select(SOURCE.c.option_code).where(
                SOURCE.c.option_code==source,SOURCE.c.option_type=='SOURCE',SOURCE.c.enabled==1).with_for_update()).first()
            if registered is None:raise ValueError('활성 SOURCE 코드가 프로젝트 DB에 없습니다')
            existing=self.db.execute(select(article_table).where(article_table.c.source_code==source,
                article_table.c.external_id==prepared['external_id']).with_for_update()).mappings().first()
            if existing:
                actual=(existing['title'],date.fromisoformat(str(existing['published_date'])),
                        existing['source_url'],existing['body_masked'],existing['category'])
                if actual!=values:raise ArticleConflict('동일 원천 ID의 내용이 다릅니다. 기존 기사와 태그를 보존했습니다')
                article_id=existing['article_id']
            else:
                row=dict(source_code=source,external_id=prepared['external_id'],
                    **dict(zip(('title','published_date','source_url','body_masked','category'),values)))
                # 신규 기사에만 최초 수신 시각·관리자 기본값·정책 스냅샷을 공급합니다.
                row=article_insert_rows(self.db,article_table,[row])[0]
                result=self.db.execute(article_table.insert().values(**row))
                article_id=result.inserted_primary_key[0]
            metadata = (existing.get('admin_meta_json') or {}) if existing else {}
            for tag in (prepared['tagging']['tags'] if self.include_tags else []):
                country,hs,version,keyword=(tag.get(k) for k in ('country_code','hs_code','hs_ver','keyword_text'))
                if sum(v is not None for v in (country,hs,keyword))!=1 or (hs is None)!=(version is None):
                    raise ValueError('태그는 국가·HS·키워드 중 하나여야 합니다')
                if keyword is not None and (not isinstance(keyword,str) or not keyword.strip() or len(keyword)>120):
                    raise ValueError('키워드 형식 오류')
                # 재실행해도 관리자가 확정한 국가·HS 분류에 자동 태그를 보태지 않습니다.
                if ((country is not None and metadata.get('country_tags_manual') is True)
                        or (hs is not None and metadata.get('hs_tags_manual') is True)):
                    continue
                fields={'country_code':country,'hs_code':hs,'hs_ver':version,'keyword_text':keyword}
                query=select(TAG.c.tag_id).where(TAG.c.article_id==article_id)
                for k,v in fields.items():query=query.where(TAG.c[k]==v)
                if self.db.execute(query).first() is None:
                    self.db.execute(TAG.insert().values(article_id=article_id,**fields))
        result={'article_id':article_id,'status':'existing' if existing else 'inserted'}
        return result if return_result else article_id
