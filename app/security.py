import base64
import hashlib
import hmac
import os
import secrets
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urljoin
from argon2 import PasswordHasher, exceptions
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import HTTPException
from .db import query,execute,transaction,utcnow

_hasher=PasswordHasher(time_cost=2,memory_cost=19456,parallelism=2)
_secret=os.getenv('SECRET_KEY') or secrets.token_urlsafe(48)
if os.getenv('ENVIRONMENT')=='production' and not os.getenv('SECRET_KEY'):
    raise RuntimeError('本番環境では SECRET_KEY が必要です')
_cookie=URLSafeTimedSerializer(_secret,salt='kokusai-user-session')

def hash_pw(password):
    if len(password)<10 or len(password)>128: raise ValueError('パスワードは10〜128文字にしてください')
    return _hasher.hash(password)

def check_pw(stored,password):
    try: return _hasher.verify(stored,password)
    except (exceptions.VerifyMismatchError,exceptions.VerificationError): return False

def get_session(request):
    if getattr(request.state, 'forced_session', None) is not None:
        return request.state.forced_session
    raw=request.cookies.get('kd_session','')
    if not raw: return None
    try: return _cookie.loads(raw,max_age=60*60*24*14)
    except (BadSignature,SignatureExpired): return None

def make_session(user_id=None,version=0):
    return _cookie.dumps({'uid':user_id,'version':version,'csrf':secrets.token_urlsafe(24)})

def set_session(response,value):
    response.set_cookie('kd_session',value,max_age=60*60*24*14,
         httponly=True,secure=os.getenv('SESSION_SECURE','false').lower()=='true',samesite='lax',path='/')

def user_from_request(request):
    session=get_session(request)
    if not session or not session.get('uid'): return None
    user=query('SELECT id,nickname,email,verified,role,disabled,session_version FROM users WHERE id=?',(session['uid'],),True)
    if not user or user['disabled'] or user['session_version']!=session.get('version'): return None
    return user

def csrf_token(request):
    ss=get_session(request)
    if not ss: return ''
    return ss['csrf']

def require_csrf(request,submitted):
    expected=csrf_token(request)
    if not expected or not submitted or not hmac.compare_digest(str(expected),str(submitted)):
        raise HTTPException(403,'フォームの有効期限が切れました。ページを更新してください。')

def require_user(request,verified=False,admin=False):
    user=user_from_request(request)
    if not user: raise HTTPException(401,'ログインしてください')
    if verified and not user['verified']: raise HTTPException(403,'メール認証が必要です')
    if admin and user['role']!='admin': raise HTTPException(403,'管理者権限が必要です')
    return user

def create_email_token(user_id,purpose):
    token=secrets.token_urlsafe(36)
    digest=hashlib.sha256(token.encode()).hexdigest()
    expires=(datetime.now(timezone.utc)+timedelta(hours=24 if purpose=='verify' else 1)).isoformat()
    execute('INSERT INTO email_tokens(token_hash,user_id,purpose,expires_at) VALUES(?,?,?,?)',
            (digest,user_id,purpose,expires))
    return token

def consume_email_token(token,purpose):
    digest=hashlib.sha256(token.encode()).hexdigest()
    with transaction() as con:
        record=con.execute('SELECT * FROM email_tokens WHERE token_hash=? AND purpose=?',(digest,purpose)).fetchone()
        if not record or record['used_at'] or record['expires_at'] < utcnow(): return None
        con.execute('UPDATE email_tokens SET used_at=? WHERE token_hash=?',(utcnow(),digest))
        if purpose=='verify': con.execute('UPDATE users SET verified=1 WHERE id=?',(record['user_id'],))
        return record['user_id']

def send_mail(to,subject,body):
    host=os.getenv('SMTP_HOST','')
    if not host:
        if os.getenv('ENVIRONMENT','development')=='production':
            raise RuntimeError('SMTP_HOST 未設定。本番環境で認証メールを送信できません')
        print(f'\n[開発環境のみ: 認証メール] {to}\n{subject}\n{body}\n',file=sys.stderr,flush=True)
        return
    msg=EmailMessage(); msg['Subject']=subject; msg['From']=os.getenv('SMTP_FROM') or os.getenv('SMTP_USER'); msg['To']=to
    msg.set_content(body)
    port=int(os.getenv('SMTP_PORT','587'))
    if port==465:
        with smtplib.SMTP_SSL(host,port,context=ssl.create_default_context(),timeout=15) as server:
            if os.getenv('SMTP_USER'): server.login(os.getenv('SMTP_USER'),os.getenv('SMTP_PASSWORD'))
            server.send_message(msg)
    else:
        with smtplib.SMTP(host,port,timeout=15) as server:
            if os.getenv('SMTP_TLS','true').lower()=='true': server.starttls(context=ssl.create_default_context())
            if os.getenv('SMTP_USER'): server.login(os.getenv('SMTP_USER'),os.getenv('SMTP_PASSWORD'))
            server.send_message(msg)

def send_auth_email(user_id,email,purpose,base_url):
    token=create_email_token(user_id,purpose)
    suffix='verify' if purpose=='verify' else 'reset-password'
    url=base_url.rstrip('/')+'/'+suffix+'?token='+token
    send_mail(email,'【国際通りパーキング】メールアドレス確認' if purpose=='verify' else '【国際通りパーキング】パスワード再設定',
              '次のリンクを開いて手続きを完了してください。\n'+url+'\n有効期限があります。心当たりがなければ無視してください。')
