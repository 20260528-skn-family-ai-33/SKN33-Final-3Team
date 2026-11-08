"""Repair missing inspection results from durable raw articles, without HTTP."""
import hashlib
import json
from news.preparation import prepare_article
from news.staging import PreparationStore
from news.report import validate_report_row
from news.dictionary import validate_dictionary
from news.dates import publication_date


def recover_prepared(db, root, dictionary=None):
    dictionary = validate_dictionary(dictionary or {'countries': [], 'products': [], 'keywords': []})
    prepared = PreparationStore(root / 'prepared.sqlite3')
    prepared.db.execute('CREATE TABLE IF NOT EXISTS recovery_required(url TEXT PRIMARY KEY, reason TEXT NOT NULL)')
    repaired, required = 0, []
    try:
        for row in db.execute('SELECT * FROM articles ORDER BY url'):
            try:
                article = validate_report_row(row)
                recorded = article.get('preparation')
                if not isinstance(recorded, dict) or not all(isinstance(recorded.get(k), dict)
                        for k in ('quality', 'focus', 'cleaning', 'tagging')):
                    raise ValueError('저장된 원문의 검수 근거가 없어 복구 필요')
                raw = {k: v for k, v in article.items() if k not in ('body_masked', 'preparation') and not k.startswith('_')}
                fingerprint = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                key = article['source_code'], article['source_article_id']
                ready = prepared.db.execute('SELECT raw_fingerprint,payload,eligible FROM prepared_article WHERE source_code=? AND external_id=?', key).fetchone()
                if ready and ready[0] == fingerprint:
                    try:
                        existing = json.loads(ready[1])
                    except (ValueError, TypeError):
                        existing = None
                    if (isinstance(existing, dict) and
                            all(isinstance(existing.get(k), dict) for k in ('quality', 'focus', 'cleaning', 'tagging')) and
                            existing.get('body_masked') == article.get('body_masked') and
                            existing.get('source_url') == article['source_url'] and
                            existing.get('title') == article['title'] and
                            existing.get('published_date') == publication_date(article['published_at']).isoformat() and
                            existing.get('published_at') == article['published_at'] and
                            existing.get('collected_at') == article['collected_at'] and
                            existing.get('source_code') == key[0] and existing.get('external_id') == key[1] and
                            bool(ready[2]) == bool(existing.get('eligible')) == bool(
                                existing.get('quality', {}).get('usable_body') and existing.get('focus', {}).get('accepted')) and
                            all(existing.get(k) == article.get('preparation', {}).get(k)
                                for k in ('quality', 'focus', 'cleaning', 'tagging'))):
                        with prepared.db:
                            prepared.db.execute('DELETE FROM recovery_required WHERE url=?', (row['url'],))
                        continue
                # Reproduce only the recorded policy and dictionary. Do not silently
                # approve using newer rules or guess an unavailable dictionary.
                candidate = prepare_article(raw, dictionary.get('countries', []),
                    dictionary.get('products', []), dictionary.get('keywords', []))
                if (candidate['body_masked'] != article.get('body_masked') or
                        any(candidate[k] != article.get('preparation', {}).get(k)
                            for k in ('quality', 'focus', 'cleaning', 'tagging'))):
                    raise ValueError('검수 입력·버전 불일치: 원래 사전과 검수 정책으로 복구 필요')
                prepared.save(raw, candidate)
                with prepared.db:
                    prepared.db.execute('DELETE FROM recovery_required WHERE url=?', (row['url'],))
                repaired += 1
            except (ValueError, TypeError, KeyError) as exc:
                reason = 'RECOVERY_REQUIRED: ' + str(exc)
                with prepared.db:
                    prepared.db.execute('INSERT OR REPLACE INTO recovery_required VALUES (?,?)', (row['url'], reason))
                required.append({'url': row['url'], 'reason': reason})
    finally:
        prepared.close()
    return {'repaired': repaired, 'recovery_required': required}
