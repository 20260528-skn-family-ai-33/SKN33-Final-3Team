"""새벽 예약에서 호출할 단일 진입점. 예약 생성·MySQL 적재는 하지 않습니다."""
import argparse
from datetime import date
from pathlib import Path
from news.daily import yesterday
from news import settings as defaults
from news.urls import FEEDS, DAILY_SITES
from collect import main as collect


def arguments(argv=None, now=None):
    p=argparse.ArgumentParser(description='전날 기사 수집. 약 10건은 참고 목표이며 건수 제한 없음')
    p.add_argument('--data-dir',required=True,type=Path,help='기존 요청 예산 DB 폴더')
    p.add_argument('--sites',nargs='+',choices=DAILY_SITES,default=list(DAILY_SITES),help='일일 수집 대상: 한국경제·파이낸셜뉴스')
    p.add_argument('--target-date',type=date.fromisoformat,help='중단 작업을 재개할 때 원래 대상 날짜 지정')
    p.add_argument('--feed-url',action='append',default=[],choices=[FEEDS[site] for site in DAILY_SITES],help='일일 목록은 최신 사이트맵만 사용')
    p.add_argument('--tag-dictionary',type=Path)
    a=p.parse_args(argv)
    target=a.target_date or yesterday(now)
    result=['--data-dir',str(a.data_dir),'--sites',*a.sites,'--discover','--daily-target',target.isoformat(),
            '--daily-limit',str(defaults.DAILY_REQUEST_LIMIT),'--max-articles-per-site','0']
    for url in a.feed_url:result+=['--feed-url',url]
    if a.tag_dictionary:result+=['--tag-dictionary',str(a.tag_dictionary)]
    return result

if __name__=='__main__':
    raise SystemExit(collect(arguments()))
