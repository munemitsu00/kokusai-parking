"""Kokusai Parking full-stack MVP — FastAPI + SQLite + Jinja2.
Run: python -m uvicorn app.main:app --reload
"""
import asyncio
import json
import mimetypes
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode
from io import BytesIO

from fastapi import FastAPI, Request, HTTPException, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps, UnidentifiedImageError
from .db import ROOT, init_db, db_path, query, execute, transaction, utcnow, setting, balance, audit, seed_file
from .pricing import estimate, validate_config, DEFAULT_RATE
from .comparison import reference_position, public_estimate
from .security import (hash_pw,check_pw,get_session,make_session,set_session,require_csrf,
                       require_user,user_from_request,csrf_token,send_auth_email,consume_email_token)

app = FastAPI(title='国際通りパーキング', docs_url=None, redoc_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'app'/'static'),name='static')
UPLOAD_DIR=ROOT/'uploads'
UPLOAD_DIR.mkdir(exist_ok=True)
Image.MAX_IMAGE_PIXELS=16_000_000
_rate_hits={}
def throttle(request,purpose,limit=6,period_seconds=900):
    # Single-process protection; use a shared limiter when scaling to multiple instances.
    now=time.monotonic()
    key=(purpose, request.client.host if request.client else 'unknown')
    recent=[v for v in _rate_hits.get(key,[]) if now-v < period_seconds]
    if len(recent)>=limit:
        raise HTTPException(429,'操作回数が多すぎます。しばらくしてからお試しください')
    recent.append(now)
    _rate_hits[key]=recent
    if len(_rate_hits)>3000:
        for k in list(_rate_hits):
            _rate_hits[k]=[v for v in _rate_hits[k] if now-v<period_seconds]
            if not _rate_hits[k]: del _rate_hits[k]
templates=Jinja2Templates(directory=str(ROOT/'app'/'templates'))

def is_demo_mode():
    return os.getenv('DEMO_MODE', 'false').lower() == 'true'

def public_base_url():
    return os.getenv('APP_BASE_URL') or os.getenv('RENDER_EXTERNAL_URL') or ''


@app.on_event('startup')
def startup():
    if os.getenv('ENVIRONMENT')=='production' and not public_base_url().startswith('https://'):
        raise RuntimeError('本番環境では HTTPS の APP_BASE_URL または RENDER_EXTERNAL_URL が必要です')
    if os.getenv('ENVIRONMENT')=='production' and not os.getenv('SECRET_KEY'):
        raise RuntimeError('本番環境では SECRET_KEY が必要です')
    init_db()
    if is_demo_mode():
        seed_file()

def view(request,name,**kwargs):
    user=user_from_request(request)
    return templates.TemplateResponse(request,name,{'user':user,'csrf':csrf_token(request),
        'notice':request.query_params.get('notice',''), 'ads_publisher':setting('ads_publisher',os.getenv('ADSENSE_PUBLISHER_ID','')),
        'ad_slots':json.loads(setting('ad_slots','{}')),'demo_mode':is_demo_mode(),'app_title':'国際通りパーキング',**kwargs})

def go(path,notice=None):
    if notice: path+=('?' if '?' not in path else '&')+'notice='+quote(str(notice))
    return RedirectResponse(path,status_code=303)

@app.middleware('http')
async def session_middleware(request,call_next):
    # 無料Renderの一時ディスクではユーザー情報が消えるため、公開デモの書き込みは禁止。
    if is_demo_mode() and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return JSONResponse({'error':'公開デモは閲覧専用です。会員登録・投稿・管理機能は本番データベース導入後に有効化します。'}, status_code=403)
    had_session=bool(get_session(request))
    if not had_session:
        # Anonymous CSRF secret for first page, persisted in a signed cookie.
        token=make_session()
        from .security import _cookie
        request.state.forced_session=_cookie.loads(token)
    response=await call_next(request)
    if not had_session and 'set-cookie' not in response.headers:
        set_session(response,token)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=(self)'
    if os.getenv('ENVIRONMENT')=='production':
        response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    if is_demo_mode():
        response.headers['X-Robots-Tag']='noindex, nofollow, noarchive'
    return response

@app.exception_handler(HTTPException)
async def user_error(request,exc):
    if request.url.path.startswith('/api/'):
        return JSONResponse({'error':exc.detail},status_code=exc.status_code)
    return templates.TemplateResponse(request,'error.html',{'code':exc.status_code,'error':exc.detail,
        'user':user_from_request(request),'csrf':csrf_token(request),'ads_publisher':'','ad_slots':{},'demo_mode':is_demo_mode(),'app_title':'国際通りパーキング'},status_code=exc.status_code)

async def form_data(request,csrf=True):
    data=await request.form()
    if csrf: require_csrf(request,data.get('csrf',''))
    return data

def email_ok(value):
    from email_validator import validate_email, EmailNotValidError
    try: return validate_email(value,check_deliverability=False).normalized
    except (EmailNotValidError,TypeError): raise HTTPException(400,'メールアドレスが正しくありません')

def referrer_limit(request):
    # Basic origin defense in addition to signed CSRF token
    origin=request.headers.get('origin')
    if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/') and origin.rstrip('/')!=os.getenv('APP_BASE_URL','').rstrip('/'):
        raise HTTPException(403,'許可されていない送信元です')

@app.get('/healthz')
def healthz():
    query('SELECT 1')
    return {'status':'ok'}

# ------------ Public parking pages ------------
@app.get('/',response_class=HTMLResponse)
def home(request:Request, arrive:str='', leave:str=''):
    lots=query('SELECT * FROM parkings WHERE is_published=1 AND is_closed=0 ORDER BY updated_at DESC, id')
    selected = bool(arrive or leave)
    error = None
    fees = {}
    if selected:
        try:
            start = datetime.fromisoformat(arrive)
            end = datetime.fromisoformat(leave)
            if start.tzinfo or end.tzinfo or not (0 < (end-start).total_seconds() <= 7*24*3600):
                raise ValueError()
        except (ValueError, TypeError):
            error = '入庫・出庫日時を正しく指定してください（1分〜7日以内）。'
            selected = False
        else:
            rules = {r['parking_id']:json.loads(r['config_json']) for r in query('SELECT parking_id,config_json FROM rate_rules')}
            for p in lots:
                fees[p['id']] = public_estimate(p,rules.get(p['id'], DEFAULT_RATE),arrive,leave)
            lots.sort(key=lambda p:(fees[p['id']]['amount'] is None,
                                     fees[p['id']]['amount'] if fees[p['id']]['amount'] is not None else 0,
                                     p['name']))
    marks=[]
    for p in lots:
        lat,lng,approx=reference_position(p)
        if lat is not None and lng is not None:
            marks.append({'id':p['id'],'name':p['name'],'lat':lat,'lng':lng,'approx':approx,
                          'verified':bool(p['entrance_confirmed']),
                          'price':fees.get(p['id'],{}).get('amount'),
                          'address':p['address']})
    operators=sorted(set(p['operator'] for p in lots if p['operator']))
    return view(request,'home.html',lots=lots,markers=marks,operators=operators,count=len(lots),
                arrive=arrive if selected else '',leave=leave if selected else '',
                fees=fees,comparison_enabled=selected,comparison_error=error)

@app.get('/parking/{pid}',response_class=HTMLResponse)
def parking_detail(request:Request,pid:int,arrive:str='',leave:str=''):
    lot=query('SELECT * FROM parkings WHERE id=? AND is_published=1',(pid,),True)
    if not lot: raise HTTPException(404,'駐車場が見つかりません')
    cfg_row=query('SELECT config_json FROM rate_rules WHERE parking_id=?',(pid,),True)
    cfg=json.loads(cfg_row['config_json']) if cfg_row else DEFAULT_RATE
    result=None
    if arrive and leave:
        result=public_estimate(lot,cfg,arrive,leave) if is_demo_mode() else estimate(arrive,leave,cfg)
    return view(request,'parking.html',p=lot,rate=cfg,result=result,arrive=arrive,leave=leave)

@app.get('/api/parkings')
def parkings_json():
    return [{'id':p['id'],'name':p['name'],'address':p['address'],'lat':p['lat'],'lng':p['lng'],
             'entrance_confirmed':bool(p['entrance_confirmed'])} for p in
            query('SELECT * FROM parkings WHERE is_published=1 AND is_closed=0')]

@app.get('/api/parking/{pid}/estimate')
def parking_estimate(pid:int,arrive:str,leave:str):
    row=query('SELECT config_json FROM rate_rules WHERE parking_id=?',(pid,),True)
    if not row: return {'amount':None,'reason':'料金設定がありません'}
    return estimate(arrive,leave,json.loads(row['config_json']))

# ------------ Accounts ------------
@app.get('/register')
def register_page(request:Request): return view(request,'auth.html',mode='register')

@app.post('/register')
async def register(request:Request):
    f=await form_data(request); referrer_limit(request);throttle(request,'register',5)
    name=str(f.get('nickname','')).strip()[:40]; email=email_ok(str(f.get('email','')).strip())
    pw=str(f.get('password',''))
    if len(name)<2: raise HTTPException(400,'ニックネームは2文字以上にしてください')
    try: digest=hash_pw(pw)
    except ValueError as exc: raise HTTPException(400,str(exc))
    with transaction() as con:
        if con.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():
            raise HTTPException(409,'このメールアドレスは登録済みです')
        cur=con.execute('INSERT INTO users(nickname,email,password_hash,created_at) VALUES(?,?,?,?)',
                  (name,email,digest,utcnow()))
        uid=cur.lastrowid
    try: send_auth_email(uid,email,'verify',public_base_url() or str(request.base_url))
    except Exception as exc:
        # Keep account unverified, allow a resend after SMTP config is corrected.
        return go('/login','アカウント登録済み。確認メールの送信に失敗しました。メール設定を確認してください。')
    return go('/login','確認メールを送信しました。リンクを開いて登録を完了してください。')

@app.get('/verify')
def verify(request:Request,token:str=''):
    uid=consume_email_token(token,'verify') if token else None
    return go('/login','メールアドレスを確認しました。ログインしてください。' if uid else '認証リンクが無効か期限切れです。')

@app.get('/login')
def login_page(request:Request): return view(request,'auth.html',mode='login')

@app.post('/login')
async def login(request:Request):
    f=await form_data(request); referrer_limit(request);throttle(request,'login',10)
    email=str(f.get('email','')).strip(); pw=str(f.get('password',''))
    record=query('SELECT * FROM users WHERE email=?',(email,),True)
    if not record or record['disabled'] or not check_pw(record['password_hash'],pw):
        raise HTTPException(401,'メールアドレスかパスワードが違います')
    response=go('/account' if record['role']!='admin' else '/admin')
    set_session(response,make_session(record['id'],record['session_version']))
    return response

@app.post('/logout')
async def logout(request:Request):
    await form_data(request)
    response=go('/','ログアウトしました')
    response.delete_cookie('kd_session',path='/')
    return response

@app.post('/resend-verification')
async def resend(request:Request):
    f=await form_data(request); user=require_user(request);throttle(request,'resend',3)
    if not user['verified']:
        send_auth_email(user['id'],user['email'],'verify',public_base_url() or str(request.base_url))
    return go('/account','確認メールの送信を受け付けました')

@app.get('/forgot-password')
def forgot_page(request:Request): return view(request,'auth.html',mode='forgot')

@app.post('/forgot-password')
async def forgot(request:Request):
    f=await form_data(request); referrer_limit(request);throttle(request,'forgot',4)
    email=str(f.get('email','')).strip()
    record=query('SELECT id,email FROM users WHERE email=?',(email,),True)
    if record:
        send_auth_email(record['id'],record['email'],'reset',public_base_url() or str(request.base_url))
    return go('/login','登録済みの場合、再設定メールを送信します')

@app.get('/reset-password')
def reset_page(request:Request,token:str=''):
    return view(request,'auth.html',mode='reset',token=token)

@app.post('/reset-password')
async def reset(request:Request):
    f=await form_data(request)
    try: digest=hash_pw(str(f.get('password','')))
    except ValueError as exc: raise HTTPException(400,str(exc))
    uid=consume_email_token(str(f.get('token','')),'reset')
    if not uid: raise HTTPException(400,'再設定リンクが無効または期限切れです')
    execute('UPDATE users SET password_hash=?,session_version=session_version+1 WHERE id=?',(digest,uid))
    return go('/login','パスワードを変更しました')

@app.get('/account')
def account(request:Request):
    user=require_user(request)
    sub=query('SELECT s.*,p.name as parking_name FROM submissions s LEFT JOIN parkings p ON s.parking_id=p.id WHERE s.user_id=? ORDER BY s.id DESC LIMIT 30',(user['id'],))
    points=query('SELECT * FROM point_transactions WHERE user_id=? ORDER BY id DESC LIMIT 30',(user['id'],))
    rewards=query('SELECT * FROM rewards WHERE active=1 ORDER BY points_cost')
    history=query('SELECT r.*,w.title AS reward_title FROM redemptions r JOIN rewards w ON r.reward_id=w.id WHERE r.user_id=? ORDER BY r.id DESC',(user['id'],))
    with transaction() as con: amount=balance(con,user['id'])
    return view(request,'account.html',sub=sub,points=points,rewards=rewards,redemptions=history,
       points_balance=amount,redeem_enabled=setting('redeem_enabled','false')=='true')

@app.post('/account/profile')
async def profile(request:Request):
    f=await form_data(request); user=require_user(request)
    nick=str(f.get('nickname','')).strip()[:40]
    if len(nick)<2: raise HTTPException(400,'ニックネームを2文字以上で入力してください')
    execute('UPDATE users SET nickname=? WHERE id=?',(nick,user['id']))
    return go('/account','ニックネームを更新しました')

@app.post('/account/email')
async def change_email(request:Request):
    f=await form_data(request);user=require_user(request)
    current=query('SELECT * FROM users WHERE id=?',(user['id'],),True)
    if not check_pw(current['password_hash'],str(f.get('password',''))):
        raise HTTPException(403,'現在のパスワードが正しくありません')
    new_email=email_ok(str(f.get('new_email','')).strip())
    if new_email==user['email']: return go('/account','現在と同じメールアドレスです')
    with transaction() as con:
        if con.execute('SELECT 1 FROM users WHERE email=?',(new_email,)).fetchone():
            raise HTTPException(409,'このメールアドレスはすでに使用されています')
        con.execute('UPDATE users SET email=?,verified=0,session_version=session_version+1 WHERE id=?',(new_email,user['id']))
        audit(con,user['id'],'change_email',user['id'])
    send_auth_email(user['id'],new_email,'verify',public_base_url() or str(request.base_url))
    response=go('/login','新しいメールアドレスに確認メールを送りました。認証後にログインしてください。')
    response.delete_cookie('kd_session',path='/')
    return response

@app.post('/account/delete')
async def delete_account(request:Request):
    f=await form_data(request);user=require_user(request)
    current=query('SELECT * FROM users WHERE id=?',(user['id'],),True)
    if str(f.get('confirm',''))!='DELETE' or not check_pw(current['password_hash'],str(f.get('password',''))):
        raise HTTPException(403,'退会確認文字列とパスワードを確認してください')
    if user['role']=='admin': raise HTTPException(403,'管理者アカウントは画面から退会できません')
    photos=query('SELECT photo_path FROM submissions WHERE user_id=? AND photo_path IS NOT NULL',(user['id'],))
    with transaction() as con:
        con.execute("UPDATE users SET nickname='退会済みユーザー',email=?,password_hash=?,verified=0,disabled=1,session_version=session_version+1 WHERE id=?",
                   (f'deleted-{user["id"]}@invalid.local',hash_pw(secrets.token_urlsafe(30)),user['id']))
        con.execute("UPDATE submissions SET payload_json='{}',photo_path=NULL WHERE user_id=?",(user['id'],))
        con.execute("UPDATE redemptions SET admin_note='会員退会により受付終了',status='rejected',processed_at=? WHERE user_id=? AND status='requested'",(utcnow(),user['id']))
        audit(con,user['id'],'account_deleted',user['id'])
    for photo in photos:
        try:(UPLOAD_DIR/Path(photo['photo_path']).name).unlink(missing_ok=True)
        except OSError:pass
    response=go('/','アカウントを退会処理しました')
    response.delete_cookie('kd_session',path='/')
    return response

# ------------ Contributions ------------
@app.get('/contribute')
def contribute_page(request:Request):
    require_user(request,verified=True)
    lots=query('SELECT id,name FROM parkings WHERE is_published=1 ORDER BY name')
    return view(request,'contribute.html',lots=lots)

def save_photo(photo):
    # Called on data from UploadFile (in-memory read, size checked prior)
    if not photo: return None
    if len(photo)>5*1024*1024: raise HTTPException(413,'写真は5MB以下にしてください')
    try:
        image=Image.open(BytesIO(photo))
        if image.format not in ('JPEG','PNG','WEBP'): raise ValueError('JPEG・PNG・WebP画像のみ対応')
        image=ImageOps.exif_transpose(image)
        image.thumbnail((1600,1600))
        if image.mode not in ('RGB','L'): image=image.convert('RGB')
        elif image.mode=='L': image=image.convert('RGB')
        name=secrets.token_hex(20)+'.jpg'
        path=UPLOAD_DIR/name
        image.save(path,format='JPEG',quality=81,optimize=True)
        return name
    except (UnidentifiedImageError,ValueError,OSError,Image.DecompressionBombError):
        raise HTTPException(400,'画像を読み込めません。JPEG・PNG・WebPを使用してください')

@app.post('/contribute')
async def contribute(request:Request):
    f=await form_data(request); user=require_user(request,verified=True)
    kind=str(f.get('kind',''))
    kinds={'new','price','hours','entrance','photo','closed','other'}
    if kind not in kinds: raise HTTPException(400,'投稿種別が正しくありません')
    pid=str(f.get('parking_id','')).strip()
    if kind!='new' and not pid: raise HTTPException(400,'対象駐車場を選択してください')
    if pid and not query('SELECT 1 AS ok FROM parkings WHERE id=?',(pid,),True):
        raise HTTPException(400,'選択した駐車場が見つかりません')
    content=str(f.get('content','')).strip()
    if len(content)<10 or len(content)>4000: raise HTTPException(400,'内容は10〜4,000文字で入力してください')
    title=str(f.get('parking_name','')).strip()[:100]
    address=str(f.get('address','')).strip()[:200]
    if kind=='new' and (not title or not address): raise HTTPException(400,'新規登録には駐車場名と住所が必要です')
    file=f.get('photo')
    photo_data=await file.read(5*1024*1024+1) if isinstance(file,StarletteUploadFile) and file.filename else None
    file_name=save_photo(photo_data)
    payload={'content':content,'parking_name':title,'address':address,'checked_at':str(f.get('checked_at',''))[:30]}
    new_id=execute('INSERT INTO submissions(user_id,parking_id,kind,payload_json,photo_path,created_at) VALUES(?,?,?,?,?,?)',
                   (user['id'],int(pid) if pid else None,kind,json.dumps(payload,ensure_ascii=False),file_name,utcnow()))
    return go('/account',f'情報提供 #{new_id} を受け付けました。承認後にポイントが付与されます。')

@app.get('/submission-photo/{sid}')
def submission_photo(request:Request,sid:int):
    user=require_user(request)
    row=query('SELECT * FROM submissions WHERE id=?',(sid,),True)
    if not row or not row['photo_path']: raise HTTPException(404,'写真が見つかりません')
    if user['role']!='admin' and row['user_id']!=user['id']:
        raise HTTPException(403,'閲覧できません')
    path=UPLOAD_DIR/Path(row['photo_path']).name
    if not path.exists(): raise HTTPException(404,'ファイルがありません')
    return FileResponse(path,media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})

@app.post('/rewards/redeem')
async def redeem(request:Request):
    f=await form_data(request); user=require_user(request,verified=True)
    if setting('redeem_enabled','false')!='true': raise HTTPException(403,'現在、ポイント交換は受け付けていません')
    try: rid=int(f.get('reward_id','0'))
    except (TypeError,ValueError): raise HTTPException(400,'特典を選択してください')
    with transaction() as con:
        reward=con.execute('SELECT * FROM rewards WHERE id=? AND active=1',(rid,)).fetchone()
        if not reward: raise HTTPException(400,'交換対象の特典が見つかりません')
        if balance(con,user['id'])<reward['points_cost']: raise HTTPException(400,'ポイントが不足しています')
        cur=con.execute('INSERT INTO redemptions(user_id,reward_id,points_used,created_at) VALUES(?,?,?,?)',
           (user['id'],rid,reward['points_cost'],utcnow()))
        con.execute('INSERT INTO point_transactions(user_id,amount,reason,redemption_id,created_at) VALUES(?,?,?,?,?)',
          (user['id'],-reward['points_cost'],f'交換申請: {reward["title"]}',cur.lastrowid,utcnow()))
        audit(con,user['id'],'redeem_requested',cur.lastrowid)
    return go('/account','交換申請を受け付けました')

# ------------ Admin ------------
def require_admin(request): return require_user(request,admin=True)

@app.get('/admin')
def admin(request:Request):
    require_admin(request)
    stats={
        'parkings':query('SELECT COUNT(*) AS n FROM parkings',one=True)['n'],
        'members':query('SELECT COUNT(*) AS n FROM users',one=True)['n'],
        'pending':query('SELECT COUNT(*) AS n FROM submissions WHERE status="pending"',one=True)['n'],
        'redemptions':query('SELECT COUNT(*) AS n FROM redemptions WHERE status="requested"',one=True)['n']}
    pending=query('SELECT s.id,s.kind,s.created_at,u.nickname,p.name as parking_name FROM submissions s JOIN users u ON s.user_id=u.id LEFT JOIN parkings p ON s.parking_id=p.id WHERE s.status="pending" ORDER BY s.id DESC LIMIT 6')
    return view(request,'admin.html',stats=stats,pending=pending)

@app.get('/admin/parkings')
def admin_parkings(request:Request):
    require_admin(request)
    lots=query('SELECT * FROM parkings ORDER BY id DESC')
    return view(request,'admin_parkings.html',lots=lots)

@app.get('/admin/parkings/new')
def new_parking(request:Request):
    require_admin(request)
    return view(request,'admin_parking_edit.html',p=None,config=DEFAULT_RATE)

@app.get('/admin/parkings/{pid}/edit')
def edit_parking(request:Request,pid:int):
    require_admin(request)
    p=query('SELECT * FROM parkings WHERE id=?',(pid,),True)
    if not p: raise HTTPException(404,'駐車場が見つかりません')
    rule=query('SELECT config_json FROM rate_rules WHERE parking_id=?',(pid,),True)
    return view(request,'admin_parking_edit.html',p=p,config=json.loads(rule['config_json']) if rule else DEFAULT_RATE)

@app.post('/admin/parkings/save')
async def save_parking(request:Request):
    f=await form_data(request); user=require_admin(request)
    try:
        pid=int(f.get('parking_id','0'))
        config=validate_config(json.loads(str(f.get('rate_config','{}'))))
        lat=float(f.get('lat')) if str(f.get('lat','')).strip() else None
        lng=float(f.get('lng')) if str(f.get('lng','')).strip() else None
        cap=int(f.get('capacity')) if str(f.get('capacity','')).strip() else None
        if lat is not None and not -90<=lat<=90: raise ValueError('緯度が不正です')
        if lng is not None and not -180<=lng<=180: raise ValueError('経度が不正です')
        if cap is not None and cap<0: raise ValueError('台数が不正です')
    except (ValueError,TypeError,KeyError,json.JSONDecodeError) as exc: raise HTTPException(400,'入力形式に問題があります: '+str(exc))
    name=str(f.get('name','')).strip()[:150];address=str(f.get('address','')).strip()[:250]
    if not name or not address: raise HTTPException(400,'名前・住所は必須です')
    source_url=str(f.get('source_url','')).strip()[:400]
    if source_url and not source_url.startswith(('https://','http://')): raise HTTPException(400,'出典URLは https:// または http:// のみ対応します')
    props=(name,str(f.get('operator',''))[:100],address,lat,lng,int(f.get('entrance_confirmed','0')=='1'),
           str(f.get('hours',''))[:100],cap,str(f.get('pricing_description',''))[:3000],
           str(f.get('payment_methods',''))[:200],str(f.get('notes',''))[:3000],
           source_url,int(f.get('is_published','0')=='1'),int(f.get('is_closed','0')=='1'))
    with transaction() as con:
        if pid:
            if not con.execute('SELECT 1 FROM parkings WHERE id=?',(pid,)).fetchone(): raise HTTPException(404,'駐車場がありません')
            con.execute('''UPDATE parkings SET name=?,operator=?,address=?,lat=?,lng=?,entrance_confirmed=?,hours=?,capacity=?,pricing_description=?,payment_methods=?,notes=?,source_url=?,is_published=?,is_closed=?,updated_at=? WHERE id=?''',(*props,utcnow(),pid))
        else:
            cur=con.execute('''INSERT INTO parkings(name,operator,address,lat,lng,entrance_confirmed,hours,capacity,pricing_description,payment_methods,notes,source_url,is_published,is_closed,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(*props,utcnow(),utcnow()))
            pid=cur.lastrowid
        con.execute('''INSERT INTO rate_rules(parking_id,config_json,updated_at) VALUES(?,?,?) ON CONFLICT(parking_id) DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at''',
                    (pid,json.dumps(config,ensure_ascii=False),utcnow()))
        audit(con,user['id'],'parking_save',pid)
    return go(f'/admin/parkings/{pid}/edit','駐車場データを保存しました')

@app.post('/api/admin/rate-preview')
async def preview_rates(request:Request):
    f=await form_data(request);require_admin(request)
    try: cfg=json.loads(str(f.get('rate_config','{}')))
    except json.JSONDecodeError: return {'amount':None,'reason':'料金設定のJSONが不正です','breakdown':[]}
    # Preview explicitly verifies temporary settings, separate from saved published status.
    cfg['verified_for_calculation']=True
    return estimate(str(f.get('arrive')),str(f.get('leave')),cfg)

@app.get('/admin/submissions')
def admin_submissions(request:Request):
    require_admin(request)
    sub=query('SELECT s.*,u.nickname,u.email,p.name AS parking_name FROM submissions s JOIN users u ON s.user_id=u.id LEFT JOIN parkings p ON s.parking_id=p.id ORDER BY CASE WHEN s.status="pending" THEN 0 ELSE 1 END,s.id DESC LIMIT 300')
    for r in sub:r['payload']=json.loads(r['payload_json'])
    return view(request,'admin_submissions.html',subs=sub)

@app.post('/admin/submissions/{sid}/review')
async def review_submission(request:Request,sid:int):
    f=await form_data(request); user=require_admin(request)
    state=str(f.get('decision',''))
    if state not in ('approved','rejected','more_info'): raise HTTPException(400,'審査結果が不正です')
    try: points=int(f.get('points','0'))
    except ValueError: raise HTTPException(400,'ポイントは整数で入力してください')
    if not 0<=points<=10000: raise HTTPException(400,'ポイント上限は10,000です')
    with transaction() as con:
        sub=con.execute('SELECT * FROM submissions WHERE id=?',(sid,)).fetchone()
        if not sub: raise HTTPException(404,'投稿が見つかりません')
        if sub['status'] not in ('pending','more_info'): raise HTTPException(409,'すでに審査済みの投稿です')
        con.execute('UPDATE submissions SET status=?,award_points=?,admin_note=?,reviewed_at=?,reviewed_by=? WHERE id=?',
            (state,points if state=='approved' else 0,str(f.get('note',''))[:1000],utcnow(),user['id'],sid))
        if state=='approved' and points:
            con.execute('INSERT INTO point_transactions(user_id,amount,reason,submission_id,created_at) VALUES(?,?,?,?,?)',
                (sub['user_id'],points,'情報提供の承認',sid,utcnow()))
        # No automatic transfer of arbitrary untrusted member text into public parking data.
        # Admin edits/adds verified data explicitly through parking form.
        audit(con,user['id'],'submission_'+state,sid,f'{points}pt')
    return go('/admin/submissions','審査結果を保存しました。駐車場の公開データは管理画面で反映してください。')

@app.get('/admin/rewards')
def admin_rewards(request:Request):
    require_admin(request)
    rewards=query('SELECT * FROM rewards ORDER BY id DESC')
    requests=query('''SELECT r.*,u.nickname,u.email,w.title AS reward_title FROM redemptions r JOIN users u ON r.user_id=u.id JOIN rewards w ON r.reward_id=w.id ORDER BY CASE WHEN r.status='requested' THEN 0 ELSE 1 END,r.id DESC LIMIT 100''')
    return view(request,'admin_rewards.html',rewards=rewards,requests=requests,enabled=setting('redeem_enabled','false')=='true')

@app.post('/admin/rewards/settings')
async def reward_settings(request:Request):
    f=await form_data(request);user=require_admin(request)
    enabled='true' if f.get('enabled')=='on' else 'false'
    with transaction() as con:
        con.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('redeem_enabled',enabled))
        audit(con,user['id'],'redeem_enabled',enabled)
    return go('/admin/rewards','交換受付設定を保存しました')

@app.post('/admin/rewards/create')
async def reward_create(request:Request):
    f=await form_data(request); user=require_admin(request)
    title=str(f.get('title','')).strip()[:100]
    try: cost=int(f.get('cost',''));yen=int(f.get('yen','0'))
    except (ValueError,TypeError): raise HTTPException(400,'ポイントは数字で入力してください')
    if not title or not 1<=cost<=10000000 or yen<0: raise HTTPException(400,'特典の設定を確認してください')
    rid=execute('INSERT INTO rewards(title,points_cost,yen_value,active) VALUES(?,?,?,0)',(title,cost,yen))
    return go('/admin/rewards',f'特典 #{rid} を作成しました（初期設定は非公開）')

@app.post('/admin/rewards/{rid}/toggle')
async def reward_toggle(request:Request,rid:int):
    await form_data(request);require_admin(request)
    execute('UPDATE rewards SET active=1-active WHERE id=?',(rid,))
    return go('/admin/rewards','特典の公開状態を変更しました')

@app.post('/admin/redemptions/{rid}/review')
async def redemption_review(request:Request,rid:int):
    f=await form_data(request);user=require_admin(request)
    state=str(f.get('decision',''))
    if state not in ('sent','rejected'): raise HTTPException(400,'処理状態が正しくありません')
    with transaction() as con:
        rec=con.execute('SELECT * FROM redemptions WHERE id=?',(rid,)).fetchone()
        if not rec: raise HTTPException(404,'申請がありません')
        if rec['status']!='requested': raise HTTPException(409,'処理済みの申請です')
        con.execute('UPDATE redemptions SET status=?,admin_note=?,processed_at=?,processed_by=? WHERE id=?',
                    (state,str(f.get('note',''))[:1000],utcnow(),user['id'],rid))
        if state=='rejected':
            con.execute('INSERT INTO point_transactions(user_id,amount,reason,created_at) VALUES(?,?,?,?)',
                        (rec['user_id'],rec['points_used'],f'交換申請 #{rid} 却下による返還',utcnow()))
        audit(con,user['id'],'redemption_'+state,rid)
    return go('/admin/rewards','交換申請を処理しました。送付完了の場合は別途ご本人へ安全に連絡してください。')

@app.get('/admin/users')
def admin_users(request:Request):
    require_admin(request)
    users=query('''SELECT u.id,u.nickname,u.email,u.verified,u.role,u.disabled,u.created_at,
      COALESCE((SELECT SUM(t.amount) FROM point_transactions t WHERE t.user_id=u.id),0) AS points
      FROM users u ORDER BY u.id DESC LIMIT 300''')
    return view(request,'admin_users.html',users=users)

@app.post('/admin/users/{uid}/points')
async def adjust_points(request:Request,uid:int):
    f=await form_data(request);user=require_admin(request)
    try: amount=int(f.get('amount'))
    except (TypeError,ValueError): raise HTTPException(400,'数字を入力してください')
    reason=str(f.get('reason','')).strip()[:150]
    if not reason or not -10000<=amount<=10000 or not amount: raise HTTPException(400,'調整額と理由を確認してください')
    with transaction() as con:
        if not con.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone(): raise HTTPException(404,'会員がありません')
        if balance(con,uid)+amount<0: raise HTTPException(400,'残高をマイナスにできません')
        con.execute('INSERT INTO point_transactions(user_id,amount,reason,created_at) VALUES(?,?,?,?)',
          (uid,amount,'管理者調整: '+reason,utcnow()))
        audit(con,user['id'],'adjust_points',uid,f'{amount}: {reason}')
    return go('/admin/users','ポイントを調整しました')

@app.get('/admin/ads')
def admin_ads(request:Request):
    require_admin(request)
    return view(request,'admin_ads.html',publisher=setting('ads_publisher',''),slots=json.loads(setting('ad_slots','{}')))

@app.post('/admin/ads')
async def admin_ads_save(request:Request):
    f=await form_data(request);user=require_admin(request)
    publisher=str(f.get('publisher','')).strip()
    if publisher and not re.fullmatch(r'ca-pub-\d{12,20}',publisher): raise HTTPException(400,'Publisher ID形式が不正です')
    slots={}
    for key in ('top','list','detail','footer'):
        value=str(f.get('slot_'+key,'')).strip()
        if value and not re.fullmatch(r'\d{6,20}',value): raise HTTPException(400,'広告スロットIDが不正です')
        slots[key]={'id':value,'enabled':f.get('enabled_'+key)=='on'}
    with transaction() as con:
        for key,value in [('ads_publisher',publisher),('ad_slots',json.dumps(slots))]:
            con.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
        audit(con,user['id'],'update_ads','settings')
    return go('/admin/ads','広告設定を保存しました。AdSense審査通過後に配信を確認してください。')

# ------------ Legal / crawling ------------
@app.get('/guide')
def guide(request:Request): return view(request,'legal.html',kind='guide')
@app.get('/privacy')
def privacy(request:Request): return view(request,'legal.html',kind='privacy')
@app.get('/terms')
def terms(request:Request): return view(request,'legal.html',kind='terms')
@app.get('/contact')
def contact(request:Request): return view(request,'legal.html',kind='contact')

@app.get('/robots.txt')
def robots(request:Request):
    if is_demo_mode():
        return Response('User-agent: *\nDisallow: /\n',media_type='text/plain')
    return Response('User-agent: *\nDisallow: /admin\nDisallow: /account\nDisallow: /contribute\nDisallow: /submission-photo\nSitemap: '+str(request.base_url)+'sitemap.xml\n',media_type='text/plain')

@app.get('/sitemap.xml')
def sitemap(request:Request):
    base=str(request.base_url).rstrip('/')
    lots=query('SELECT id FROM parkings WHERE is_published=1 AND is_closed=0')
    urls=['/','/guide','/privacy','/terms']+[f'/parking/{p["id"]}' for p in lots]
    from xml.sax.saxutils import escape
    return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+
                    ''.join('<url><loc>'+escape(base+u)+'</loc></url>' for u in urls)+'</urlset>',media_type='application/xml')

@app.get('/ads.txt')
def ads_txt():
    code=setting('ads_publisher',os.getenv('ADSENSE_ACCOUNT_ID',''))
    if not code: return Response('# AdSense未設定\n',media_type='text/plain')
    return Response('google.com, '+code.removeprefix('ca-')+', DIRECT, f08c47fec0942fa0\n',media_type='text/plain')
