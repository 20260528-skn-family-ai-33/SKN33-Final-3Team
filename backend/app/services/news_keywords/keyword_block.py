"""사용자 키워드 블락: 불용어 대신 사용자가 보기 싫은 키워드를 직접 막습니다 (회의 결정 2026-10-14).

블락은 키워드 정리·집계·판정 결과를 바꾸지 않고, 사용자에게 보여 줄 때만 거릅니다.
- 저장: 사용자가 넣은 말을 동의어 사전 → 상위어 사전 순으로 최종 키워드로 바꿔 저장
  (EV를 블락하면 '전기차' → 동의어 표기 기사까지 함께 숨김. 전기차보조금도 키워드가 전기차라 '전기차'로 저장)
- 기사 목록·검색 결과: kw_topic에 블락한 대표어가 하나라도 있으면 숨김 (검색 흐름에서는 후보 검색 직후, LLM 재순위 전에 적용)
- 주요 이슈(FR-204)·급증 알림(FR-203)·속보 강조(FR-202): 블락한 키워드 줄을 뺌
- 나라 이름은 키워드가 아니라 국가 칸(country_all)에 있어 블락 대상이 아님 (관심 국가 설정으로 처리)
서비스에서는 user_keyword_block(user_id, keyword) 테이블을 읽어 같은 일을 합니다. 테이블 설계는 키워드블락_검토.md.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("keyword_clean", Path(__file__).resolve().parent / "keyword_clean.py")
kc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kc)


def to_block_word(word, syn=None, hyp=None):
    """사용자가 입력한 말 → 저장할 블락 키워드(정제 → 동의어 대표어 → 상위어). 예: 'ev' → '전기차'."""
    w = kc.normalize(word)
    syn = kc.load_synonyms() if syn is None else syn
    hyp = kc.load_hypernyms() if hyp is None else hyp
    w = syn.get(w, w)
    return hyp.get(w, w)


def hide_articles(df, blocked, col="kw_topic"):
    """기사 표에서 블락한 키워드가 하나라도 있는 기사를 뺍니다."""
    blocked = set(blocked)
    if not blocked:
        return df
    return df[[not (set(str(s).split("|")) & blocked) for s in df[col]]]


def hide_keys(rows, blocked, key="key"):
    """주요 이슈 표(DataFrame)나 급증 알림 목록(dict 목록)에서 블락한 키워드 줄을 뺍니다."""
    blocked = set(blocked)
    if isinstance(rows, list):
        return [r for r in rows if r[key] not in blocked]
    return rows[~rows[key].isin(blocked)]
