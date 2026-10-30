"""FastAPI 진입점. 명세 경로와 인증 정책은 contracts.json에서 읽는다."""
import json
import logging
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.app.api.common import ApiError, clean, require
from backend.app.api.security import authenticate, administrator, limit_auth_request, limit_public, client_ip, hash_token
from backend.app.api.routes import dispatch, PENDING
from backend.app.api.schemas import Conditions, HsItem

CONTRACTS = json.loads(Path(__file__).with_name('contracts.json').read_text(encoding='utf-8'))
log = logging.getLogger(__name__)


def validate_value(value, kind, name, query=False):
    if value is None:
        require('|null' in kind, f'{name}: null을 허용하지 않습니다.')
        return None
    kind = kind.split('|')[0]
    if kind.endswith('[]'):
        require(isinstance(value, list) and len(value) <= 200, f'{name}: 최대 200개의 배열이 필요합니다.')
        return [validate_value(v, kind[:-2], name, query) for v in value]
    if kind == 'boolean':
        if query and value in ('true', 'false'): return value == 'true'
        require(type(value) is bool, f'{name}: boolean 값이 필요합니다.')
    elif kind in ('integer', 'number'):
        if query:
            try: value = int(value) if kind == 'integer' else float(value)
            except (ValueError, TypeError): raise ApiError(400, 'VALIDATION_ERROR', f'{name}: 숫자가 필요합니다.')
        require(type(value) in ((int,) if kind == 'integer' else (int, float)), f'{name}: 숫자가 필요합니다.')
        if name in ('page', 'page_size', 'limit', 'months', 'article_limit', 'region_id', 'category_id'):
            require(value > 0, f'{name}: 양수가 필요합니다.')
        if name == 'page_size': require(value <= 100, 'page_size는 100 이하입니다.')
        if name == 'months': require(value <= 120, 'months는 120 이하입니다.')
    elif kind == 'string':
        require(isinstance(value, str) and len(value) <= 10000, f'{name}: 문자열 길이를 확인하세요.')
        if name not in ('q', 'other_reason'): require(bool(value.strip()), f'{name}: 빈 값을 허용하지 않습니다.')
        if name in ('country_codes', 'country_code'): require(bool(re.fullmatch(r'[A-Z]{2}', value)), '국가 코드는 대문자 두 자리입니다.')
        if name in ('start_date', 'end_date', 'news_start_date', 'news_end_date'):
            try: date.fromisoformat(value)
            except ValueError: raise ApiError(400, 'VALIDATION_ERROR', f'{name}: 유효한 YYYY-MM-DD가 필요합니다.')
        if name == 'through':
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                require(parsed.tzinfo is not None, 'through에는 시간대가 필요합니다.')
            except ValueError: raise ApiError(400, 'VALIDATION_ERROR', 'through: 유효한 일시가 필요합니다.')
    elif kind == 'Conditions': return Conditions.model_validate(value).model_dump(mode='json')
    elif kind == 'HsItem': return HsItem.model_validate(value).model_dump(mode='json')
    elif kind in ('ArticleLinkInput', 'ConsentAcceptance'):
        require(isinstance(value, dict), f'{name}: 객체가 필요합니다.')
    return value


def parse_input(contract, body, params, path):
    inputs = [('Request Body', body, False), ('Query Parameters', params, True), ('Path Parameters', path, False)]
    outputs = []
    for location, data, query in inputs:
        fields = {f[1]: f for f in contract['fields'] if f[0] == location and f[3] != '제외'}
        require(set(data) <= set(fields), f'{location}: 지원하지 않는 필드가 있습니다.')
        parsed = {}
        for name, field in fields.items():
            if name not in data:
                require(field[3] != '필수', f'{name}: 필수 항목입니다.')
                continue
            if query:
                values = data.getlist(name)
                require(field[2].endswith('[]') or len(values) == 1, f'{name}: 중복 값을 허용하지 않습니다.')
                value = values if field[2].endswith('[]') else values[0]
            else: value = data[name]
            parsed[name] = validate_value(value, field[2], name, query or location=='Path Parameters')
            if location=='Path Parameters' and name in ('user_id','user_alert_id','event_id','report_id','job_id','article_id','session_id','subscription_id','run_id'):
                require(bool(re.fullmatch(r'[1-9]\d{0,19}',str(parsed[name]))) and int(parsed[name])<=18446744073709551615,f'{name}: 올바른 식별자가 필요합니다.')
        for a, b in [('start_date','end_date'), ('news_start_date','news_end_date')]:
            require(not parsed.get(a) or not parsed.get(b) or parsed[a] <= parsed[b], '시작일은 종료일 이하여야 합니다.')
        outputs.append(parsed)
    return outputs


def openapi_fields(contract):
    properties = {}; required = []; parameters = []
    for location, name, kind, needed, description in contract['fields']:
        if needed == '제외': continue
        base = kind.split('|')[0]
        primitive = base if base in ('integer','number','boolean','string') else 'object'
        schema = {'type': primitive}
        if base.endswith('[]'):
            element = base[:-2]
            schema = {'type':'array','items':{'type':element if element in ('string','integer') else 'object'}}
        if location == 'Request Body':
            properties[name] = {**schema, 'description':description}
            if needed == '필수': required.append(name)
        else:
            parameters.append({'name':name,'in':'path' if location == 'Path Parameters' else 'query',
                               'required':needed == '필수', 'schema':schema,'description':description})
    extra = {'parameters':parameters}
    if properties:
        extra['requestBody'] = {'required':bool(required),'content':{'application/json':{'schema':{
            'type':'object','additionalProperties':False,'properties':properties,'required':required}}}}
    return extra


def create_app(settings=None, engine=None, redis_state=None):
    settings = settings or Settings()
    db = Database(engine or settings.engine())
    db.settings = settings
    from backend.app.api.redis_store import RedisState
    db.redis = redis_state or RedisState(settings)
    app = FastAPI(title='Borderless Sellers API', version='0.18.0')
    app.state.database = db; app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True,
        allow_methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'],
        allow_headers=['Content-Type','X-CSRF-Token','Idempotency-Key'],
        expose_headers=['Content-Disposition','Retry-After','X-Request-ID'])

    @app.middleware('http')
    async def request_context(request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def error_response(request, error):
        headers = {'Retry-After':'60'} if error.status == 429 else None
        return JSONResponse(status_code=error.status, headers=headers, content={
            'code':error.code,'message':error.message,'details':error.details,
            'retryable':error.retryable,'request_id':request.state.request_id})

    @app.exception_handler(ApiError)
    async def api_error(request, error): return error_response(request, error)

    @app.exception_handler(ValidationError)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic input 값(비밀번호 포함)을 응답/로그에 복사하지 않는다.
        return error_response(request, ApiError(400,'VALIDATION_ERROR','요청 형식 또는 값을 확인하세요.',
            [{'field':'.'.join(map(str,e['loc'])),'reason':e['type']} for e in error.errors()]))

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(request, ApiError(error.status_code,'NOT_FOUND' if error.status_code == 404 else 'HTTP_ERROR','요청 경로 또는 메서드를 확인하세요.'))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        # 드라이버 예외 문자열에는 연결 주소/SQL/개인정보가 포함될 수 있다.
        log.warning('database_failure request_id=%s type=%s',request.state.request_id,type(error).__name__)
        return error_response(request, ApiError(409 if isinstance(error,IntegrityError) else 503,
            'CONFLICT' if isinstance(error,IntegrityError) else 'DATABASE_UNAVAILABLE',
            '저장 충돌이 발생했습니다.' if isinstance(error,IntegrityError) else '데이터베이스 연결 또는 저장 구조를 확인하세요.',retryable=True))

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        log.error('request_failed request_id=%s type=%s',request.state.request_id,type(error).__name__)
        return error_response(request,ApiError(500,'INTERNAL_ERROR','요청 처리에 실패했습니다. request_id로 운영자에게 문의하세요.'))

    @app.get('/health/live', include_in_schema=False)
    def live(): return {'status':'ok'}

    @app.get('/health/ready', include_in_schema=False)
    def ready():
        with db.engine.connect() as connection: connection.execute(text('SELECT 1'))
        from backend.app.api.runtime_schema import check_schema
        check_schema(db)
        db.redis.ping()
        return {'status':'ready'}

    def endpoint(contract):
        async def handle(request: Request, response: Response):
            chunks = []; size = 0
            async for chunk in request.stream():
                size += len(chunk)
                require(size <= 65536, '요청 본문은 64KiB 이하입니다.', 'REQUEST_TOO_LARGE', 413)
                chunks.append(chunk)
            raw = b''.join(chunks)
            if raw:
                require(request.headers.get('content-type','').split(';')[0] == 'application/json','JSON 본문이 필요합니다.','UNSUPPORTED_MEDIA_TYPE',415)
                try: body = json.loads(raw)
                except (ValueError, UnicodeDecodeError): raise ApiError(400,'VALIDATION_ERROR','올바른 JSON이 필요합니다.')
                require(isinstance(body,dict),'요청 본문은 객체여야 합니다.')
            else: body = {}
            body, query, path = parse_input(contract, body, request.query_params, request.path_params)
            if request.method not in ('GET','HEAD','OPTIONS'):
                origin = request.headers.get('origin')
                require(origin is None or origin in settings.origins,'허용하지 않은 Origin입니다.','ORIGIN_FORBIDDEN',403)
                require(request.headers.get('sec-fetch-site') != 'cross-site' or origin in settings.origins,'교차 사이트 요청을 허용하지 않습니다.','ORIGIN_FORBIDDEN',403)
            def execute():
                identity = contract['id']; auth = contract['auth']
                if identity==11:
                    limit_public(db,hash_token(f'HS_SEARCH:ip:{client_ip(request,settings)}'),
                        maximum=int(settings.get('API_HS_IP_LIMIT',60)))
                if identity in (1,2,69,72,73,74,128,129,130) and identity not in PENDING:
                    limit_auth_request(db,request,settings,identity,body)
                with db.engine.begin() as connection:
                    store = Store(db,connection)
                    required = auth in ('필요','ADMIN','개발 역할 + 환경 허용') or query.get('scope') == 'INTERESTED'
                    user = None
                    if required or (request.cookies.get('bs_session') and identity not in (1,2,69,70,71,72,73,74,128,129,130)):
                        try: user = authenticate(store,request,required)
                        except ApiError as error:
                            if required or error.status != 401: raise
                    if 'ADMIN' in auth: administrator(user)
                    result = dispatch(identity,store,user,body,query,path,request,response,settings)
                    if isinstance(result,Response): return result
                    if result is None: response.status_code = 204; return response
                    if identity in (36,38,44): response.status_code = 202
                    elif identity in (1,34): response.status_code = 201
                    return clean(result)
            return await run_in_threadpool(execute)
        return handle

    active = [c for c in CONTRACTS if c['auth'] != '호출 없음']
    # 정적 경로가 {id}에 잡히지 않게 먼저 등록한다.
    for contract in sorted(active,key=lambda c:(c['route'].count('{'),-len(c['route']))):
        method, path = contract['route'].split()
        app.add_api_route(path,endpoint(contract),methods=[method],name=f"api_{contract['id']:03}",
            operation_id=f"API_{contract['id']:03}",summary=contract['title'],openapi_extra=openapi_fields(contract),
            description=('미연결: '+PENDING[contract['id']]) if contract['id'] in PENDING else contract['auth'])
    return app
