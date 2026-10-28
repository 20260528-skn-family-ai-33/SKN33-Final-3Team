"""원문 버전과 서비스용 결과를 원자적으로 저장하는 로컬 준비 저장소입니다.

프로젝트 MySQL 적재 전 단계입니다. 트래픽 예산·수집 대기열을 만들거나 복제하지 않습니다.
"""
import hashlib
import json
import sqlite3


class PreparationStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS raw_version (
          source_code TEXT NOT NULL, external_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
          payload TEXT NOT NULL, PRIMARY KEY(source_code, external_id, fingerprint));
        CREATE TABLE IF NOT EXISTS prepared_article (
          source_code TEXT NOT NULL, external_id TEXT NOT NULL, raw_fingerprint TEXT NOT NULL,
          eligible INTEGER NOT NULL CHECK(eligible IN (0,1)), payload TEXT NOT NULL,
          PRIMARY KEY(source_code,external_id),
          FOREIGN KEY(source_code,external_id,raw_fingerprint)
          REFERENCES raw_version(source_code,external_id,fingerprint));
        CREATE TABLE IF NOT EXISTS prepared_tag (
          source_code TEXT NOT NULL, external_id TEXT NOT NULL, tag_key TEXT NOT NULL,
          payload TEXT NOT NULL, PRIMARY KEY(source_code,external_id,tag_key),
          FOREIGN KEY(source_code,external_id) REFERENCES prepared_article(source_code,external_id));
        ''')

    def save(self, original, prepared):
        key = original['source_code'], original['source_article_id']
        if key != (prepared['source_code'], prepared['external_id']):
            raise ValueError('원문과 준비 결과 식별자 불일치')
        if original['source_url'] != prepared['source_url']:
            raise ValueError('원문 URL 불일치')
        encoded = json.dumps(original, ensure_ascii=False, sort_keys=True)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        # 기사와 태그 저장 중 하나라도 실패하면 원문 버전 추가까지 함께 취소합니다.
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO raw_version VALUES(?,?,?,?)', (*key, fingerprint, encoded))
            self.db.execute('INSERT INTO prepared_article VALUES(?,?,?,?,?) ON CONFLICT(source_code,external_id) DO UPDATE SET raw_fingerprint=excluded.raw_fingerprint,eligible=excluded.eligible,payload=excluded.payload',
                            (*key, fingerprint, int(prepared['eligible']), json.dumps(prepared, ensure_ascii=False)))
            self.db.execute('DELETE FROM prepared_tag WHERE source_code=? AND external_id=?', key)
            for tag in prepared['tagging']['tags']:
                tag_key = json.dumps([tag['kind'], tag.get('country_code'), tag.get('hs_code'), tag.get('hs_ver'), tag.get('keyword_text')])
                self.db.execute('INSERT INTO prepared_tag VALUES(?,?,?,?)', (*key, tag_key, json.dumps(tag, ensure_ascii=False)))

    def close(self):
        self.db.close()
