"""교육·모의 활동과 실제 교역 사실을 구분하는 합성 회귀 시험입니다."""
import pytest
from news.focus import assess_focus


@pytest.mark.parametrize('body', [
    '학생들이 모의 무역대회에서 미국 시장 수출 계약을 체결했다.',
    '이번 경진대회는 해외시장 진출 전략을 발표하는 자리다.',
    '학생들이 수출 잠재력이 있는 K-소비재 발굴을 주제로 전략을 발표했다.',
    '수출전문위원의 사전 평가 30%와 현장 평가 70%를 반영했다.',
    'FTA·무역 실무교육과 원산지실무사 교육을 지원한다.',
    '기업의 해외시장 진출 교육을 지원하고 수강생을 모집한다.',
    '교육에서는 미국 관세를 피하기 위한 전기차 현지 생산을 설명했다.',
    '강의에서는 중국산 전기차에 관세를 부과하는 절차를 설명한다.',
    '수출 교육을 운영하는 계약을 체결했다.',
    '해외시장 진출 교육 지원 계약을 체결했다.',
])
def test_training_and_simulation_are_not_real_activity(body):
    result = assess_focus({'title': '수출·관세 소식'}, body)
    assert not result['accepted']
    assert not result['activity_evidence']
    assert not result['concrete_evidence']
    assert result['ignored_activity_evidence']


@pytest.mark.parametrize('body', [
    '무역교육을 받은 기업은 미국 바이어와 수출 계약을 체결했다.',
    '교육용 장비를 미국에 수출하는 계약을 체결했다.',
    '교육에 참가한 기업의 원산지·통관 애로를 해결했다.',
    '관세청은 기업의 원산지·통관 애로를 해결했다.',
    '수출 실무교육을 진행했다. 기업은 미국 바이어와 수출 계약을 체결했다.',
    '모의 무역대회를 개최했다. 기업은 미국 바이어와 수출 계약을 체결했다.',
    '모의 무역대회를 개최했다.\n기업은 미국 바이어와 수출 계약을 체결했다.',
    '국내 업체는 탄자니아에 유량계를 수출하는 계약을 체결했다.',
    '국내 기업은 수상 운송으로 미국 반도체 수출 계약을 이행했다.',
    '해당 기업은 100억원 규모의 수출 계약을 취소했다.',
    '정부는 기업의 수출 계약 심사를 지원했다.',
    '장관상 수상자는 미국 바이어와 수출 계약을 체결했다.',
    '기업은 본선 적재 방식으로 수출 계약을 체결했다.',
])
def test_independent_real_activity_is_preserved(body):
    result = assess_focus({'title': '교육·대회 소식'}, body)
    assert result['accepted']
    assert all(row['text'] in body for row in result['activity_evidence'])


def test_gdp_rule_and_low_ratio_are_unchanged():
    # 사용자 요청대로 이번 수정에서는 거시경제·일반 수치 판정 규칙을 바꾸지 않습니다.
    body = '설비투자와 생산성을 분석했다.\n' * 20 + '수출 4강, 국민소득 5만 달러 목표를 제시했다.'
    assert assess_focus({'title': '잠재성장률 전망'}, body)['accepted']


def test_body_and_evidence_are_not_rewritten():
    body = 'FTA 원산지 교육을 지원한다.\n한국 기업은 미국 반도체 수출 계약을 체결했다.'
    original = body
    result = assess_focus({'title': '현장 소식'}, body)
    assert body == original
    assert result['accepted']
    assert result['ignored_activity_evidence'][0]['text'] == body.splitlines()[0]
    assert result['activity_evidence'][0]['text'] == body.splitlines()[1]
