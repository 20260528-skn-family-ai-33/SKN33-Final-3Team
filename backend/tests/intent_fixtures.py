"""Recorded slot fixtures for deterministic integration tests; never a production fallback."""
from copy import deepcopy

BASE={'intent':'STATISTICS','countries':[],'products':[],'period':None,'recent_months':None,
      'compare_period':None,'trade_flow':'EXPORT','ranking_group':None,'all_countries':False,'all_products':True}
DEC={'start':'2025-12-01','end':'2025-12-31'}
JAN={'start':'2025-01-01','end':'2025-01-31'}
FIXTURES={
    '2025년 12월 전체 품목 수출 추이':{'period':DEC},
    '2025년 12월 전체 품목 수출':{'period':DEC},
    '2025년 12월 미국 전체 품목 수출 추이':{'period':DEC,'countries':[{'mention':'미국','name':'미국'}]},
    '2025년 12월 화장품 수출':{'period':DEC,'products':['화장품'],'all_products':False},
    '2025년 1월 전체 품목 기사':{'intent':'LOCAL_ISSUES','period':JAN},
    '전체 품목 기사':{'intent':'LOCAL_ISSUES'},
    '최근 2개월 전체 품목 기사':{'intent':'LOCAL_ISSUES','recent_months':2},
    '수출 추이를 알려줘':{'all_products':False},
}


def extracted_slots(*,question,previous_conditions):
    return deepcopy({**BASE,**FIXTURES.get(question,{})})
