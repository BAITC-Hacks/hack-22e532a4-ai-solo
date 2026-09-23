"""Optional jury gate. Public preview is read-only; production data/API require a session."""
import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from threading import Lock
from urllib.parse import unquote
import posixpath

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

COOKIE='baqbaq_jury'
VISITOR_COOKIE='baqbaq_workspace'
_attempts=defaultdict(deque)
_lock=Lock()

def configured():
    return not public_live() and bool(os.getenv('BAQBAQ_AUTH_PASSWORD_HASH') or os.getenv('BAQBAQ_AUTH_SECRET'))

def public_live():
    return os.getenv('BAQBAQ_PUBLIC_LIVE','0') == '1'

async def public_gate(request, call_next):
    """No login, but every browser has its own unguessable document namespace."""
    path=posixpath.normpath(unquote(request.url.path))
    if request.method not in {'GET','HEAD','OPTIONS'}:
        origin=request.headers.get('origin')
        if request.headers.get('sec-fetch-site')=='cross-site' or (origin and origin.rstrip('/') not in {'https://'+request.headers.get('host',''),'http://'+request.headers.get('host','')}):
            return JSONResponse({'detail':'Запрос должен быть выполнен с этого сайта'},status_code=403)
    if path.startswith('/demo/') or path.endswith('/demo') or path in {'/landing.js','/landing.css'}:
        return JSONResponse({'detail':'Не найдено'},status_code=404)
    visitor=request.cookies.get(VISITOR_COOKIE,'')
    fresh=len(visitor)!=64 or any(c not in '0123456789abcdef' for c in visitor)
    if fresh: visitor=secrets.token_hex(32)
    owner=hashlib.sha256(visitor.encode()).hexdigest()
    request.state.owner_key=owner
    store=request.app.state.store
    parts=path.strip('/').split('/')
    comparison_id=None
    if len(parts)>=3 and parts[:2]==['api','comparisons']: comparison_id=parts[2]
    elif len(parts)>=3 and parts[:2]==['api','findings']:
        with store.connect() as db:
            row=db.execute('SELECT comparison_id FROM findings WHERE id=?',(parts[2],)).fetchone()
            comparison_id=row['comparison_id'] if row else '__missing__'
    if comparison_id and not store.owns_comparison(comparison_id,owner):
        return JSONResponse({'detail':'Сравнение не найдено в этом рабочем пространстве'},status_code=404)
    response=await call_next(request)
    if fresh:
        secure=request.url.scheme=='https' or request.headers.get('x-forwarded-proto')=='https'
        response.set_cookie(VISITOR_COOKIE,visitor,max_age=7*24*3600,httponly=True,secure=secure,samesite='lax',path='/')
    if path.startswith('/api/') or path in {'/','/workspace','/index.html'}: response.headers['Cache-Control']='no-store'
    return response

def password_hash(password, salt=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),240000).hex()
    return salt+':'+digest

def check_password(password):
    stored=os.getenv('BAQBAQ_AUTH_PASSWORD_HASH','')
    salt,_,_=stored.partition(':')
    return bool(stored) and hmac.compare_digest(password_hash(password,salt),stored)

def token():
    payload=f'{int(time.time())+8*3600}.{secrets.token_hex(16)}'
    signature=hmac.new(os.environ['BAQBAQ_AUTH_SECRET'].encode(),payload.encode(),'sha256').hexdigest()
    return payload+'.'+signature

def authorized(request):
    if not configured(): return True
    cookie=request.cookies.get(COOKIE,'')
    try:
        expires,nonce,signature=cookie.split('.')
        expected=hmac.new(os.environ['BAQBAQ_AUTH_SECRET'].encode(),f'{expires}.{nonce}'.encode(),'sha256').hexdigest()
        if int(expires)>time.time() and hmac.compare_digest(expected,signature): return True
    except (ValueError,KeyError): pass
    header=request.headers.get('authorization','')
    if header.startswith('Basic '):
        try:
            user,password=base64.b64decode(header[6:],validate=True).decode().split(':',1)
            return user=='jury' and check_password(password)
        except (ValueError,UnicodeError): pass
    return False

def login_allowed(request):
    address=request.client.host if request.client else 'unknown'
    with _lock:
        now=time.monotonic(); queue=_attempts[address]
        while queue and queue[0]<now-60: queue.popleft()
        if len(queue)>=10: return False
        queue.append(now)
    return True

async def gate(request: Request, call_next):
    if public_live(): return await public_gate(request,call_next)
    if not configured(): return await call_next(request)
    if not os.getenv('BAQBAQ_AUTH_SECRET') or not os.getenv('BAQBAQ_AUTH_PASSWORD_HASH'):
        return JSONResponse({'detail':'Серверный доступ не настроен'},status_code=503)
    path=posixpath.normpath(unquote(request.url.path))
    public=path in {'/','/index.html','/login.html','/landing.js','/landing.css','/login.js','/styles.css','/api/session'} or path.startswith(('/assets/','/demo/'))
    if request.method not in {'GET','HEAD','OPTIONS'}:
        origin=request.headers.get('origin')
        if request.headers.get('sec-fetch-site')=='cross-site' or (origin and origin.rstrip('/') not in {'https://'+request.headers.get('host',''),'http://'+request.headers.get('host','')}):
            return JSONResponse({'detail':'Запрос должен быть выполнен с этого сайта'},status_code=403)
    if not public and not authorized(request):
        if path in {'/workspace','/workspace.html'}: return RedirectResponse('/login.html',status_code=303)
        return JSONResponse({'detail':'Войдите с доступом жюри на /login.html'},status_code=401)
    response=await call_next(request)
    if not public: response.headers['Cache-Control']='no-store'
    return response
