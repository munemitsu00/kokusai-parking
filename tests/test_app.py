import json
import re
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db,transaction,query,utcnow
from app.security import hash_pw,create_email_token
import app.main as main_module

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'test.sqlite3'))
    monkeypatch.setenv('ENVIRONMENT','development')
    init_db()
    with TestClient(app) as c:
        yield c

def get_csrf(client,path='/'):
    response=client.get(path)
    soup=BeautifulSoup(response.text,'html.parser')
    target=soup.select_one('input[name="csrf"]')
    if not target:return None
    return target.get('value')

def new_admin(client):
    with transaction() as con:
        con.execute('INSERT INTO users(nickname,email,password_hash,verified,role,created_at) VALUES (?,?,?,?,?,?)',
                    ('Admin','admin@local.test',hash_pw('admin-long-pass-999'),1,'admin',utcnow()))
    token=get_csrf(client,'/login')
    r=client.post('/login',data={'csrf':token,'email':'admin@local.test','password':'admin-long-pass-999'},follow_redirects=False)
    assert r.status_code==303


def test_public_pages(client):
    for route in ['/','/register','/login','/guide','/privacy','/terms','/ads.txt','/sitemap.xml']:
        assert client.get(route).status_code==200
    assert client.get('/admin').status_code==401
    assert client.get('/contribute').status_code==401


def test_csrf(client):
    response=client.post('/register',data={'nickname':'test','email':'test@demo.io','password':'12345678900'})
    assert response.status_code==403


def test_register_verify_and_contribute(client,monkeypatch):
    sent=[]
    monkeypatch.setattr(main_module,'send_auth_email',lambda uid,email,purpose,base:sent.append(create_email_token(uid,purpose)))
    token=get_csrf(client,'/register')
    r=client.post('/register',data={'csrf':token,'nickname':'利用者','email':'user@demo.io','password':'super-secret-pass-888'},follow_redirects=False)
    assert r.status_code==303 and len(sent)==1
    token=get_csrf(client,'/login')
    r=client.post('/login',data={'csrf':token,'email':'user@demo.io','password':'super-secret-pass-888'},follow_redirects=False)
    assert r.status_code==303
    assert client.get('/contribute').status_code==403
    assert client.get('/verify?token='+sent[0]).status_code==200
    assert client.get('/contribute').status_code==200
    token=get_csrf(client,'/contribute')
    r=client.post('/contribute',data={'csrf':token,'kind':'new','parking_name':'新しいテスト駐車場',
        'address':'沖縄県那覇市牧志1丁目','content':'実際に現地で確認した新しい駐車場です。2026年10月24日に看板を確認しました。'},follow_redirects=False)
    assert r.status_code==303
    assert query('SELECT status FROM submissions',one=True)['status']=='pending'
    assert client.get('/admin').status_code==403


def test_admin_parking_and_points(client):
    new_admin(client)
    assert client.get('/admin').status_code==200
    form=client.get('/admin/parkings/new')
    token=get_csrf(client,'/admin/parkings/new')
    cfg={'weekday':{'minutes':30,'yen':100},'weekend':{'minutes':20,'yen':200},
         'periods':[], 'caps':[{'type':'daypart','start':'08:00','end':'20:00','yen':700,'repeat':True}],
         'verified_for_calculation':True}
    data={'csrf':token,'parking_id':0,'name':'テスト駐車場','address':'那覇市松尾1丁目',
          'operator':'個人','is_published':'1','is_closed':'0','rate_config':json.dumps(cfg)}
    r=client.post('/admin/parkings/save',data=data,follow_redirects=False)
    assert r.status_code==303
    lot=query('SELECT * FROM parkings WHERE name=?',('テスト駐車場',),one=True)
    assert lot
    estimate=client.get(f'/api/parking/{lot["id"]}/estimate',params={'arrive':'2026-10-26T10:00','leave':'2026-10-26T18:00'}).json()
    assert estimate['amount']==700
    # Create pending submission and award once only.
    with transaction() as con:
        con.execute('INSERT INTO users(nickname,email,password_hash,verified,created_at) VALUES(?,?,?,?,?)',
                    ('Writer','writer@local.test',hash_pw('long-user-password-7'),1,utcnow()))
        uid=con.execute('SELECT id FROM users WHERE email="writer@local.test"').fetchone()[0]
        cur=con.execute('INSERT INTO submissions(user_id,kind,payload_json,created_at) VALUES(?,?,?,?)',(uid,'other','{"content":"test"}',utcnow()))
        sid=cur.lastrowid
    token=get_csrf(client,'/admin/submissions')
    r=client.post(f'/admin/submissions/{sid}/review',data={'csrf':token,'decision':'approved','points':'100'},follow_redirects=False)
    assert r.status_code==303
    assert query('SELECT SUM(amount) AS p FROM point_transactions WHERE user_id=?',(uid,),one=True)['p']==100
    r=client.post(f'/admin/submissions/{sid}/review',data={'csrf':token,'decision':'approved','points':'100'})
    assert r.status_code==409
    assert query('SELECT SUM(amount) AS p FROM point_transactions WHERE user_id=?',(uid,),one=True)['p']==100

def test_ads_txt_uses_pub_id(client):
    from app.db import execute
    execute('INSERT INTO settings(key,value) VALUES(?,?)',('ads_publisher','ca-pub-1234567890123456'))
    data=client.get('/ads.txt').text
    assert 'google.com, pub-1234567890123456, DIRECT' in data
    assert 'ca-pub-' not in data


def test_email_change_reauth_and_delete(client,monkeypatch):
    sent=[]
    monkeypatch.setattr(main_module,'send_auth_email',lambda uid,email,purpose,base:sent.append(create_email_token(uid,purpose)))
    token=get_csrf(client,'/register')
    client.post('/register',data={'csrf':token,'nickname':'Hana','email':'hana@demo.io','password':'super-secret-pass-888'})
    token=get_csrf(client,'/login')
    client.post('/login',data={'csrf':token,'email':'hana@demo.io','password':'super-secret-pass-888'})
    token=get_csrf(client,'/account')
    r=client.post('/account/email',data={'csrf':token,'new_email':'hana.new@demo.io','password':'super-secret-pass-888'},follow_redirects=False)
    assert r.status_code==303
    assert query('SELECT verified FROM users WHERE email=?',('hana.new@demo.io',),True)['verified']==0
    assert client.get('/account').status_code==401
    client.get('/verify?token='+sent[-1])
    token=get_csrf(client,'/login')
    client.post('/login',data={'csrf':token,'email':'hana.new@demo.io','password':'super-secret-pass-888'})
    token=get_csrf(client,'/account')
    r=client.post('/account/delete',data={'csrf':token,'confirm':'DELETE','password':'super-secret-pass-888'},follow_redirects=False)
    assert r.status_code==303
    assert client.get('/account').status_code==401
    assert query('SELECT id FROM users WHERE email=?',('hana.new@demo.io',),True) is None
