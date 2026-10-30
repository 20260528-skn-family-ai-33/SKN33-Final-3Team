"""API 정의서의 경로별 구현 연결. 성공 응답 예시 데이터를 실행에 사용하지 않는다."""
import csv
import io
import json
from datetime import date
from pathlib import Path
from urllib.parse import quote
from sqlalchemy import select, update, delete, func
from fastapi.responses import Response, FileResponse
from backend.app.api import accounts, reference, trade, news, notifications, reports, chat, un_market, admin, jobs
from backend.app.api.common import ApiError, require, unavailable, now, clean, digest, page_info, percentage, month, shift_month, period
from backend.app.api.security import issue_session, hash_token, administrator
from backend.app.api.schemas import Signup, Login, PasswordChange, InterestUpdate, NotificationPatch, ReportCreate, Conditions
from backend.app.api.results import get_table, read_snapshot, result_table
from backend.app.api.files import xlsx_bytes, MIMES


# 이 기능들은 원문에서 계약/정책 확정 전 활성화를 금지한 항목이다.
PENDING={
  8:'DEC-46 탈퇴 사유 및 보존·삭제 정책',
  23:'ML 담당 협의 보류',27:'ML 담당 협의 보류',
  69:'DEC-34 소셜 계정 저장·연결 및 OAuth 제공자 설정',70:'DEC-34 소셜 계정 저장·연결 및 OAuth 제공자 설정',
  71:'DEC-34 소셜 신규 가입 저장 계약',72:'DEC-34 이메일 인증·메일 제공자 설정',73:'DEC-34 이메일 인증·메일 제공자 설정',74:'DEC-34 소셜 신규 가입·동의 저장 계약',
  75:'DEC-34 일반 동의 종류와 과거 약관 보존 계약',76:'DEC-34 일반 동의 종류와 과거 약관 보존 계약',
  79:'DEC-37 과거 월말 회원·구독 스냅샷 저장 계약',
  88:'DEC-42 상세 로그·실패 범위 생산자 계약',89:'데이터 적재 담당자의 실패 범위 재수집 실행기 연결',
  90:'DEC-38 HS 판본 품질 판정 저장 계약',91:'DEC-41 버전 획득 및 데이터 담당 재검증 실행기 연결',
  94:'태그 변경 이후 집계·벡터 인덱스 반영 실행기 연결',
  97:'DEC-32 키워드 수동 검수 상태·출처 저장 계약',98:'DEC-32 키워드 검수 근거 저장 계약',
  99:'DEC-49 과거 기사 분류 보존 및 검수 실행기 연결',
  101:'DEC-32 기본/관리자 분야 출처 저장 계약',102:'DEC-32 관리자 분야 출처 저장 계약',
  103:'DEC-32 관리자 분야 출처 저장 계약',104:'DEC-32 관리자 분야 출처 저장 계약',105:'DEC-49 분야 삭제·이관 정책',
  125:'DEC-41 버전 획득 및 적재 담당자의 출처 중지 실행기 연결',
  128:'DEC-43/44 계정 후보 노출·복구 운영 기준',129:'DEC-44 메일 발송·반복 제한·복구 기준',130:'DEC-44 임시 비밀번호 발급·만료·메일 실패 처리 기준',
}


def file_response(content,filename,fmt):
    return Response(content,media_type=MIMES[fmt],headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename),'Cache-Control':'private, no-store'})


def _day(value):
    return date.fromisoformat(value) if value else None


def dispatch(identity,store,user,body,query,path,request,response,settings):
    uid=user['user_id'] if user else None
    page=query.get('page',1); size=query.get('page_size',20)
    if identity in PENDING:
        raise ApiError(422 if identity in (23,27) else 503,'FEATURE_NOT_AVAILABLE' if identity in (23,27) else 'FEATURE_NOT_READY',PENDING[identity])
    if identity==1: return accounts.signup(store,Signup.model_validate(body),response,settings)
    if identity==2: return accounts.login(store,Login.model_validate(body),response,settings)
    if identity==3:
        if user:
            store.db.redis.revoke(user['token_hash'])
        response.delete_cookie('bs_session',path='/'); return None
    if identity==4: return {'user':accounts.user_view(store,user),'csrf_token':hash_token('csrf:'+request.cookies['bs_session'])}
    if identity==5:
        landing=body['landing_page']; require(landing in ('GLOBAL','INTERESTED'),'시작 화면을 확인하세요.')
        scope=accounts.interests(store,uid)
        require(landing!='INTERESTED' or scope['hs_items'] or scope['country_codes'],'관심 등록이 필요합니다.','INTERESTS_REQUIRED',409)
        t=store.t('users'); store.c.execute(update(t).where(t.c.user_id==uid).values(landing_page=landing))
        return {'user':accounts.user_view(store,{**user,'landing_page':landing})}
    if identity==6: return {'matches':body['email'].strip().lower()==user['email'].lower()}
    if identity==7:
        accounts.change_password(store,user,PasswordChange.model_validate(body)); response.delete_cookie('bs_session',path='/'); return None
    if identity==9:
        t=store.t('service_option'); options=store.rows(select(t.c.option_code,t.c.label_ko).where(t.c.option_type=='REPORT_PERIOD',t.c.enabled==True).order_by(t.c.sort_order))
        return {'report_periods':options,'supported_formats':['PDF','DOCX','XLSX'],
            'capabilities':[{'code':f'API-{i:02}','enabled':True} for i in range(1,131) if i not in PENDING and i not in range(106,117)],
            'password_policy':'영문·숫자·특수문자를 포함한 8자 이상, UTF-8 72바이트 이하, 확인값 일치'}
    if identity==10: return trade.availability(store)
    if identity==11: return reference.hs_search(store,query['q'],query.get('depth',4),query.get('limit',5))
    if identity==12: return reference.children(store,path['hs_code'],query['hs_ver'])
    if identity==13: return reference.countries(store,query.get('q'),query.get('region_id'))
    if identity==14: return reference.regions(store)
    if identity==15: return {'interests':accounts.interests(store,uid)}
    if identity==16: return accounts.save_interests(store,uid,InterestUpdate.model_validate(body))
    if identity==17:
        conditions=read_snapshot(store,uid,body['result_id'],'RESULT')['conditions']; saved=accounts.interests(store,uid)
        hs=conditions.get('hs_items',[]); require(all(len(x['hs_code'])==6 for x in hs),'관심 등록은 HS6만 허용합니다.','INVALID_HS_DEPTH')
        all_hs=list({(x['hs_code'],x['hs_ver']):x for x in saved['hs_items']+hs}.values())
        all_c=sorted(set(saved['country_codes'])|set(conditions.get('country_codes',[])))
        already=len(all_hs)==len(saved['hs_items']) and all_c==saved['country_codes']
        result=accounts.save_interests(store,uid,InterestUpdate(hs_items=all_hs,country_codes=all_c,expected_version=saved['version']))
        return {**result,'already_registered':already}
    if identity==18: return trade.kpis(store,uid,query.get('month'))
    if identity in (19,20,21):
        hs,countries=trade.scope_filters(store,uid,query.get('scope','GLOBAL'),query.get('hs_code'),query.get('hs_ver'),query.get('country_codes'))
        start,end=query.get('period_start'),query.get('period_end')
        if identity==19:
            require(not ('months' in query and (start or end)),'months와 직접 기간은 함께 지정할 수 없습니다.')
            return trade.trends(store,uid,start,end,query.get('months',24),hs,countries,query.get('trade_flow','BOTH'))
        if identity==21:
            countries=[path['country_code']]; accounts.validate_scope(store,hs,countries)
            if query.get('month'):
                require(not start and not end,'month와 기간은 함께 지정할 수 없습니다.'); start=end=query['month']
        result=trade.rankings(store,uid,start,end,hs,countries,'HS6' if identity==21 else query.get('group_by','COUNTRY'),query.get('sort_flow','EXPORT'),page,5 if identity==21 else query.get('page_size',5))
        if identity==21: return {'items':result['items'],'meta':result['meta']}
        return result
    if identity in (22,24): return changes(store,uid,body if identity==24 else query,identity==22)
    if identity in (25,26): return trade.market_shares(store,uid,Conditions.model_validate(body['conditions']).model_dump(mode='json'),identity==26)
    if identity==28:
        c=Conditions.model_validate(body['conditions']).model_dump(mode='json'); require(c['hs_items'] or c['country_codes'],'국가 또는 품목이 필요합니다.')
        p=c['period']; trend=trade.trends(store,uid,p['start'][:7],p['end'][:7],hs=c['hs_items'],countries=c['country_codes'])
        articles=news.articles(store,uid,hs=c['hs_items'],countries=c['country_codes'],start=_day(body.get('news_start_date')),end=_day(body.get('news_end_date')),size=10)
        return {'articles':articles['items'],'trend':trend['items'],'table':get_table(store,uid,trend['result_id'],1,1000),
                'meta':{**trend['meta'],'warnings':['RAG 미연결: 기사 본문 발췌만 제공합니다.']}}
    if identity==29:
        hs=[{'hs_code':query['hs_code'],'hs_ver':query.get('hs_ver')}] if query.get('hs_code') else []
        return news.articles(store,uid,page,size,tab=query.get('tab','ALL'),q=query.get('q'),start=_day(query.get('start_date')),end=_day(query.get('end_date')),
            scope=query.get('scope','GLOBAL'),countries=query.get('country_codes'),hs=hs,mode=query.get('mode','BROWSE'),category_boost=query.get('category_boost_ids'))
    if identity==30: return news.article_detail(store,path['article_id'],uid)
    if identity==31: return notifications.major_issues(store,uid,page,size)
    if identity==32: return news.exchange_rates(store,uid,query.get('scope','GLOBAL'),query.get('currency_codes'))
    if identity==33: return chat.sessions(store,uid,page,size)
    if identity==34: return chat.create(store,uid,body.get('initial_conditions'))
    if identity==35: return chat.messages(store,uid,path['session_id'],page,size)
    if identity==36: return chat.submit(store,uid,path['session_id'],body)
    if identity==37: return {'job':chat.view_job(jobs.owned_job(store,uid,path['job_id']))}
    if identity==38: return chat.clarification(store,uid,path['job_id'],body)
    if identity in (39,40,64):
        sid=path['session_id']; store.owned('chat_session','session_id',sid,uid,lock=True); t=store.t('chat_session')
        if identity==39: store.c.execute(update(t).where(t.c.session_id==sid).values(context_json={'conditions':{},'interest_action':'CLEARED'})); return None
        if identity==64:
            require(0<len(body['title'].strip())<=160,'대화 제목은 1~160자입니다.')
            store.c.execute(update(t).where(t.c.session_id==sid).values(title=body['title'].strip(),updated_at=now()))
            return {'session_id':str(sid),'title':body['title'].strip()}
        # 실행 상태도 메시지에 속하므로 세션 잠금 아래 메시지 삭제로 함께 제거한다.
        m=store.t('chat_message'); snapshots=store.t('api_snapshot')
        records=store.rows(select(m.c.result_json).where(m.c.session_id==sid,m.c.result_json.is_not(None)))
        ids={table['result_id'] for record in records for table in (record['result_json'].get('answer') or {}).get('tables',[]) if table.get('result_id')}
        if ids: store.c.execute(delete(snapshots).where(snapshots.c.user_id==uid,snapshots.c.snapshot_id.in_(ids)))
        store.c.execute(delete(m).where(m.c.session_id==sid)); store.c.execute(delete(t).where(t.c.session_id==sid)); return None
    if identity==41: return {'table':get_table(store,uid,path['result_id'],page,size)}
    if identity==42:
        payload=read_snapshot(store,uid,path['result_id'],'RESULT'); return file_response(xlsx_bytes([payload],payload['meta'],payload['sql']),'분석결과.xlsx','xlsx')
    if identity==43: return reports.preview(store,uid,body)
    if identity==44: return reports.create(store,uid,ReportCreate.model_validate(body),request.headers.get('Idempotency-Key'))
    if identity==45: return {'job':reports.status_view(store,store.owned('report','report_id',path['report_id'],uid))}
    if identity==46: return reports.listing(store,uid,query.get('q'),query.get('report_type'),query.get('status'),page,size)
    if identity==47: return reports.read_report(store,uid,path['report_id'])
    if identity==48:
        rid=path['report_id']; fmt=path['format'].lower(); require(fmt in MIMES,'지원하지 않는 파일 형식입니다.','UNSUPPORTED_FORMAT',422)
        record=store.owned('report','report_id',rid,uid); require(record['status']=='READY','파일 생성 중입니다.','REPORT_NOT_READY',409)
        if fmt=='xlsx':
            content=record['content_json']; tables=[t for s in content['sections'] for t in s['tables']]
            return file_response(xlsx_bytes(tables,content['meta'],content.get('sql',[])),'리포트.xlsx',fmt)
        file=(record.get('files_json') or {}).get(fmt.upper())
        require(file is not None,'선택하지 않았거나 준비되지 않은 형식입니다.','NOT_FOUND',404)
        from backend.app.api.report_storage import LocalReportStorage
        return file_response(LocalReportStorage(settings.files).read(file),'리포트.'+fmt,fmt)

    if identity==49: return reports.subscription_list(store,uid)
    if identity==50: return reports.save_subscriptions(store,uid,body)
    if identity==51:
        store.owned('report_subscription','subscription_id',path['subscription_id'],uid)
        t=store.t('report_subscription'); store.c.execute(update(t).where(t.c.subscription_id==path['subscription_id'],t.c.user_id==uid).values(enabled=False)); return None
    if identity==52: return {'settings':notifications.settings(store,uid)}
    if identity==53: return notifications.patch_settings(store,uid,NotificationPatch.model_validate(body).model_dump(exclude_unset=True))
    if identity==54:
        require(path['terms_type'].lower()=='notification','일반 약관 저장 계약이 준비되지 않았습니다.','FEATURE_NOT_READY',503)
        return {'terms':notifications.terms(store)}
    if identity==55: return notifications.consent(store,uid,body)
    if identity==56: return notifications.listing(store,uid,query.get('type','ALL'),query.get('unread_only',False),page,size)
    if identity==57: return {'count':notifications.unread_count(store,uid),'as_of':now().isoformat()+'Z'}
    if identity==58:
        row=notifications.notification_one(store,uid,path['user_alert_id'])
        return {'notification':notifications.notification_views(store,[row],uid)[0]}
    if identity==59: return notifications.read_one(store,uid,path['user_alert_id'])
    if identity==60: return notifications.read_all(store,uid,body['through'],body.get('type','ALL'))
    if identity==61: return notifications.popups(store,uid)
    if identity==62: notifications.acknowledge(store,uid,path['delivery_id']); return None
    if identity==63:
        require(settings.get('API_DEBUG_SQL','false')=='true' and user['role']=='ADMIN','개발 모드 SQL 접근 권한이 없습니다.','FORBIDDEN',403)
        p=read_snapshot(store,uid,path['result_id'],'RESULT'); return {'sql':'\n'.join(p['sql']),'elapsed_ms':None,'row_count':p['total_rows']}
    if identity==65: return reference.categories(store)
    if identity==66: return reference.topics(store,query.get('q'),page,size)
    if identity==67: return news.blocks(store,uid)
    if identity==68: return news.save_blocks(store,uid,body['keywords'],body['expected_version'])
    if identity==77: return un_market.availability(store,query['hs6_code'],query['hs_ver'],query.get('country_codes',[]),query['freq_code'])
    if identity==78: return un_market.shares(store,body)
    if identity==80:
        t=store.t('batch_run'); rows=store.rows(select(t).where(t.c.status=='FAILED').order_by(t.c.run_id.desc()))
        return {'items':[{'alert_id':str(r['run_id']),'category':'DATA','message':'수집 작업 실패: '+r['job_name'],
            'target_type':'BATCH','target_id':str(r['run_id']),'created_at':r['finished_at']} for r in rows[(page-1)*size:page*size]],'page':page_info(page,size,len(rows))}
    if identity==81: return admin.users(store,query.get('q'),query.get('status','ALL'),page,size)
    if identity==82: return admin.detail(store,path['user_id'])
    if identity in (83,84): return admin.change_user(store,user,path['user_id'],body,identity==83)
    if identity==85: admin.user_row(store,path['user_id']); return {'interests':accounts.interests(store,path['user_id']),'checks':[]}
    if identity==86:
        admin.user_row(store,path['user_id']); scope=accounts.interests(store,path['user_id'])
        return {'direct_country_codes':scope['country_codes'],'articles':news.articles(store,int(path['user_id']),scope='INTERESTED',start=_day(query.get('start_date')),end=_day(query.get('end_date')))['items'],'checks':[]}
    if identity==87: return admin.runs(store,page,size,query.get('source_code'),query.get('status'))
    if identity==92:
        t=store.t('article'); stmt=select(t.c.article_id)
        if query.get('q'): stmt=stmt.where(t.c.title.contains(query['q'],autoescape=True) | t.c.body_masked.contains(query['q'],autoescape=True))
        if query.get('visibility'): stmt=stmt.where(t.c.visibility_status=='VISIBLE' if query['visibility']=='VISIBLE' else t.c.visibility_status=='HIDDEN')
        total=store.count(stmt); ids=store.c.execute(stmt.order_by(t.c.article_id.desc()).offset((page-1)*size).limit(size)).scalars()
        return {'items':[admin.article_detail(store,i)['moderation'] for i in ids],'page':page_info(page,size,total)}
    if identity==93: return admin.article_detail(store,path['article_id'])
    if identity==95: return admin.change_visibility(store,user,path['article_id'],body)
    if identity==96:
        a,b,o=store.t('article'),store.t('batch_run'),store.t('service_option')
        items=[]
        for code in ('KOTRA_MARKET','KOTRA_SHORT_NEWS'):
            enabled=store.c.scalar(select(o.c.enabled).where(o.c.option_code==code))
            if enabled is None: continue
            latest=store.c.scalar(select(func.max(b.c.run_id)).where(b.c.source_code==code))
            items.append({'source_code':code,'collection_enabled':bool(enabled),'article_count':store.count(select(a).where(a.c.source_code==code)),
                'visible_count':store.count(select(a).where(a.c.source_code==code,a.c.visibility_status=='VISIBLE')),'latest_run_id':str(latest) if latest else None})
        return {'items':items}
    if identity==100:
        t=store.t('keyword_topic'); stmt=select(t.c.keyword_text,t.c.canonical_text,t.c.category_id,t.c.status)
        if query.get('status'): stmt=stmt.where(t.c.status==query['status'])
        stream=io.StringIO(); writer=csv.writer(stream); writer.writerow(['keyword_text','canonical_text','category_id','status'])
        for r in store.rows(stmt.order_by(t.c.keyword_text)):
            writer.writerow(["'"+str(v) if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in r.values()])
        return Response(stream.getvalue().encode('utf-8-sig'),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=keywords.csv'})
    if identity in (117,118):
        t,e=store.t('user_alert'),store.t('alert_event'); stmt=select(t,e.c.event_type).join(e,e.c.event_id==t.c.event_id)
        if identity==118: stmt=stmt.where(t.c.user_alert_id==path['user_alert_id'])
        if query.get('user_id'): stmt=stmt.where(t.c.user_id==query['user_id'])
        if query.get('result') and query['result']!='INCLUDED':
            require(query['result'] in ('EXCLUDED','UNKNOWN'),'전달 결과를 확인하세요.')
            unavailable('당시 제외 기록 저장 계약 DEC-37이 필요합니다.','FEATURE_NOT_READY')
        rows=store.rows(stmt.order_by(t.c.user_alert_id.desc()))
        items=[{'event_id':str(r['event_id']),'user_id':str(r['user_id']),'user_alert_id':str(r['user_alert_id']),
            'result':'INCLUDED','popup_shown_at':r['popup_shown_at'],'read_at':r['read_at']} for r in rows]
        if identity==118: require(items,'전달 기록이 없습니다.','NOT_FOUND',404); return {'delivery':items[0]}
        return {'items':items[(page-1)*size:page*size],'page':page_info(page,size,len(items))}
    if identity==119:
        target=path['user_id']; admin.user_row(store,target); c=store.t('user_consent')
        rows=store.rows(select(c).where(c.c.user_id==target).order_by(c.c.consent_id.desc()))
        return {'settings':notifications.settings(store,target),'consents':[{'terms_type':'NOTIFICATION',**{k:r[k] for k in ('terms_version','action','acted_at')},'required':True} for r in rows]}
    if identity==120:
        admin.user_row(store,query['user_id']); t=store.t('alert_event')
        require(store.c.scalar(select(t.c.event_id).where(t.c.event_id==path['event_id'])) is not None,'알림 사건이 없습니다.','NOT_FOUND',404)
        return {'diagnosis':{'basis':'UNKNOWN','reason_codes':['HISTORICAL_EXCLUSION_NOT_RECORDED'],'evaluated_at':now().isoformat()+'Z'}}
    if identity in (121,124):
        t=store.t('service_option'); types=['RULE'] if identity==121 else ['REPORT_PERIOD','RULE','SOURCE']
        rows=store.rows(select(t).where(t.c.option_type.in_(types)))
        # 자격정보가 포함될 수 있는 SOURCE JSON 전체는 노출하지 않는다.
        values=[{'option_code':r['option_code'],'label_ko':r['label_ko'],'enabled':bool(r['enabled']),
            'value_text':json.dumps(r['value_json'],ensure_ascii=False) if r['option_type']!='SOURCE' else ('수집 활성' if r['enabled'] else '수집 비활성')} for r in rows]
        return {'rules' if identity==121 else 'items':values}
    if identity==122: return admin.audit_list(store,page,size,query.get('target_type'))
    if identity in (123,127):
        t=store.t('api_audit_event'); row=store.one(select(t).where(t.c.audit_id==path.get('audit_id',path.get('operation_id'))))
        require(row is not None,'처리 이력이 없습니다.','NOT_FOUND',404)
        if identity==123: return {'audit':admin.audit_view(row)}
        return {'operation':{'operation_id':row['audit_id'],'status':'SUCCEEDED','step':'DONE','completed_count':1,'total_count':1,'error_code':None,'message':'변경 및 감사 기록 완료'}}
    if identity==126:
        t=store.t('service_option'); stmt=select(t).where(t.c.option_type=='NOTIFICATION_TERMS')
        if query.get('version'): stmt=stmt.where(t.c.option_code==query['version'])
        row=store.one(stmt.order_by(t.c.option_code.desc()).limit(1)); require(row,'약관이 없습니다.','NOT_FOUND',404)
        c=store.t('user_consent'); consent_query=select(c).where(c.c.terms_version==row['option_code'])
        if query.get('user_id'): consent_query=consent_query.where(c.c.user_id==query['user_id'])
        consents=store.rows(consent_query)
        return {'terms':{'terms_type':'NOTIFICATION','version':row['option_code'],'body':row['value_json'].get('body'),
            'effective_at':row['value_json'].get('effective_at')},'consents':[{'user_id':str(r['user_id']),'terms_type':'NOTIFICATION','terms_version':r['terms_version'],'action':r['action'],'acted_at':r['acted_at'],'required':True} for r in consents]}
    raise ApiError(503,'FEATURE_NOT_READY','이 API의 연동 계약이 준비되지 않았습니다.')


def changes(store,uid,data,products=False):
    require(products or data.get('compare') in ('MOM','YOY'),'비교 기준은 MOM/YOY입니다.')
    selected=data.get('month'); start,end=trade.resolve_period(store,selected,selected,1)
    previous=shift_month(start,-12 if products or data.get('compare','YOY')=='YOY' else -1)
    require(previous in trade.verified_months(store,'export_summary'),'비교 월 데이터가 없습니다.','DATA_NOT_READY',503)
    hs=data.get('hs_items',[]); countries=data.get('country_codes',[]); accounts.validate_scope(store,hs,countries)
    group='HS6' if products else 'PAIR'
    key=lambda r:(r.get('country_code'),r['hs6_code'])
    current={key(r):r for r in trade.aggregate(store,'export_summary',start,end,hs,countries,group)}
    old={key(r):r for r in trade.aggregate(store,'export_summary',previous,previous,hs,countries,group)}
    flags=trade.anomaly_map(store,start,end,hs,countries,group)
    option=store.t('service_option')
    configured=store.c.scalar(select(option.c.value_json).where(option.c.option_code=='TRADE_CHANGE_THRESHOLDS',option.c.enabled==True)) or {}
    defaults={'minimum_amount_usd':1000000,'minimum_delta_usd':1000000,'surge_pct':150,'drop_pct':-60,'major_pct':200}
    rule={**defaults,**{k:v for k,v in configured.items() if k in defaults}}
    require(all(type(v) in (int,float) for v in rule.values()) and rule['minimum_amount_usd']>0 and rule['minimum_delta_usd']>0 and rule['surge_pct']>0 and rule['drop_pct']<0 and rule['major_pct']>=rule['surge_pct'], '무역 변화 임계값 설정을 확인하세요.','FEATURE_NOT_READY',503)
    items=[]
    # Both months are verified above. Missing trades within those months are zero.
    for k in sorted(current.keys()|old.keys()):
        value=current.get(k,{}).get('amount_usd',0)
        base=old.get(k,{}).get('amount_usd',0)
        growth=percentage(value,base); delta=value-base
        items.append({'country_code':k[0],'hs_item':{'hs_code':k[1],'hs_ver':'HS2022'},'baseline_amount_usd':base,
            'current_amount_usd':value,'change_amount_usd':delta,'growth_pct':growth,'growth_status':'NEW' if base==0 and value>0 else 'NORMAL',
            'severity':'MAJOR' if growth is not None and growth>=rule['major_pct'] else 'NORMAL',
            'one_off':flags.get(k[1] if products else k,trade.anomaly_view(set()))})
    meta=trade.metadata(store,start,end)
    if products:
        filtered=[r for r in items if r['growth_pct'] is not None and r['current_amount_usd']>=rule['minimum_amount_usd']]
        return {'items':sorted(filtered,key=lambda r:(-r['growth_pct'],-r['change_amount_usd'],r['hs_item']['hs_code']))[:10],'meta':meta}
    items=[r for r in items if max(r['current_amount_usd'],r['baseline_amount_usd'])>=rule['minimum_amount_usd']]
    surges=[r for r in items if r['growth_pct'] is not None and r['growth_pct']>=rule['surge_pct'] and r['change_amount_usd']>=rule['minimum_delta_usd']]
    drops=[r for r in items if r['growth_pct'] is not None and r['growth_pct']<=rule['drop_pct'] and r['change_amount_usd']<=-rule['minimum_delta_usd']]
    new=[r for r in items if r['growth_status']=='NEW' and r['current_amount_usd']>=rule['minimum_amount_usd']]
    conditions=Conditions(hs_items=hs,country_codes=countries,period=period(start,end),
        compare_period=period(previous,previous),compare=data['compare'],trade_flow='EXPORT',metric='GROWTH').model_dump(mode='json')
    return {'surges':surges,'drops':drops,'new_entries':new,'table':result_table(store,uid,surges+drops+new,meta,conditions,store.queries),'meta':meta}
