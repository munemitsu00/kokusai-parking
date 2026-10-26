"""Project setup commands.
python manage.py init
python manage.py seed
python manage.py admin
"""
import argparse
import getpass
from app.db import init_db,seed_file,transaction,utcnow
from app.security import hash_pw

p=argparse.ArgumentParser();p.add_argument('command',choices=['init','seed','admin']);args=p.parse_args()
if args.command=='init':
    init_db();print('データベースを初期化しました')
elif args.command=='seed':
    init_db();n=seed_file();print(f'{n}件のテスト駐車場を登録しました（料金計算は未検証につき停止）')
elif args.command=='admin':
    init_db();email=input('管理者メールアドレス: ').strip().lower();nickname=input('管理者ニックネーム: ').strip()
    pw=getpass.getpass('管理者パスワード（10文字以上）: ')
    digest=hash_pw(pw)
    with transaction() as con:
        if con.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone():
            raise SystemExit('登録済みメールアドレスです。既存アカウントを昇格する処理は用意していません。')
        con.execute('INSERT INTO users(nickname,email,password_hash,verified,role,created_at) VALUES(?,?,?,?,?,?)',
                    (nickname,email,digest,1,'admin',utcnow()))
    print('管理者アカウントを作成しました')
