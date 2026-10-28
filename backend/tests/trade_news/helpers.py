"""실제 기사·접속 정보 없는 합성 시험 자료."""
from sqlalchemy import create_engine, event, text

def article(number=1):
    ident=f'202610210{number:04d}'
    return dict(source_code='hankyung',source_article_id=ident,
                source_url='https://www.hankyung.com/article/'+ident,
                title='시험 기업의 반도체 수출 계약',
                body='한국 시험기업은 미국에 반도체를 수출하는 100억원 계약을 체결했다.\n공유하기\n담당 연락처 sample@example.test',
                published_at='2026-10-21',collected_at='2026-10-22T03:00:00+09:00',keywords=[])

def prepared(number=1):
    from news.preparation import prepare_article
    return prepare_article(article(number))

DDL = [
"CREATE TABLE service_option (option_code VARCHAR(40) PRIMARY KEY, option_type VARCHAR(20), enabled INTEGER NOT NULL)",
"CREATE TABLE article (article_id INTEGER PRIMARY KEY, source_code VARCHAR(40) NOT NULL REFERENCES service_option(option_code), external_id VARCHAR(100) NOT NULL, title VARCHAR(600) NOT NULL, published_date DATE NOT NULL, source_url VARCHAR(2048) NOT NULL, body_masked TEXT NOT NULL, category VARCHAR(120), UNIQUE(source_code,external_id))",
"CREATE TABLE article_tag (tag_id INTEGER PRIMARY KEY, article_id INTEGER REFERENCES article(article_id), country_code VARCHAR(12), hs_code VARCHAR(10), hs_ver VARCHAR(10), keyword_text VARCHAR(120), UNIQUE(article_id,keyword_text))"]

def engine(path):
    from backend.app.db.session import make_engine
    db=make_engine('sqlite:///'+str(path))
    @event.listens_for(db,'connect')
    def foreign_keys(c,_):c.execute('PRAGMA foreign_keys=ON')
    with db.begin() as c:
        for ddl in DDL:c.execute(text(ddl))
        c.execute(text("INSERT INTO service_option VALUES ('HANKYUNG','SOURCE',1),('FNNEWS','SOURCE',1),('KOTRA_MARKET','SOURCE',1),('OTHER','SOURCE',1)"))
    return db
