"""과거 호출부 호환용 함수. 제목·요약으로 수집 대상을 제외하지 않습니다."""


def candidate_title(text, keywords):
    # 무역 관련성은 본문 수집·정제 후 focus.assess_focus에서 판단합니다.
    return True
