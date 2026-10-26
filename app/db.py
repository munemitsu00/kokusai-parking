"""SQLite persistence; use PostgreSQL before sustained multi-instance production deployments."""
import json
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data' / 'parking.sqlite3'

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def db_path():
    return Path(os.getenv('DATABASE_PATH') or DEFAULT_DB).expanduser().resolve()

def get_db():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=10000')
    return con

@contextmanager
def transaction():
    con = get_db()
    try:
        con.execute('BEGIN IMMEDIATE')
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def query(sql, params=(), one=False):
    with get_db() as con:
        cur = con.execute(sql, params)
        rows = cur.fetchall()
    items = [dict(row) for row in rows]
    return (items[0] if items else None) if one else items

def execute(sql, params=()):
    with transaction() as con:
        cur=con.execute(sql, params)
        return cur.lastrowid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 nickname TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
 role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member','admin')),
 disabled INTEGER NOT NULL DEFAULT 0, session_version INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_tokens (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset')),
 expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS parkings (
 id INTEGER PRIMARY KEY AUTOINCREMENT, external_code TEXT UNIQUE,
 name TEXT NOT NULL, operator TEXT NOT NULL DEFAULT '', address TEXT NOT NULL,
 lat REAL, lng REAL, entrance_confirmed INTEGER NOT NULL DEFAULT 0,
 hours TEXT NOT NULL DEFAULT '', capacity INTEGER,
 pricing_description TEXT NOT NULL DEFAULT '', payment_methods TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
 source_checked_at TEXT, onsite_checked_at TEXT, photo_url TEXT,
 is_published INTEGER NOT NULL DEFAULT 1, is_closed INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_rules (
 parking_id INTEGER PRIMARY KEY REFERENCES parkings(id) ON DELETE CASCADE,
 config_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
 parking_id INTEGER REFERENCES parkings(id), kind TEXT NOT NULL,
 payload_json TEXT NOT NULL, photo_path TEXT,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','more_info')),
 admin_note TEXT, award_points INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, reviewed_at TEXT, reviewed_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS point_transactions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
 amount INTEGER NOT NULL, reason TEXT NOT NULL,
 submission_id INTEGER UNIQUE REFERENCES submissions(id),
 redemption_id INTEGER UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rewards (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
 points_cost INTEGER NOT NULL CHECK(points_cost > 0), yen_value INTEGER,
 active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS redemptions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
 reward_id INTEGER NOT NULL REFERENCES rewards(id), points_used INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','sent','rejected')),
 admin_note TEXT, created_at TEXT NOT NULL, processed_at TEXT,
 processed_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER REFERENCES users(id),
 action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_parkings_pub ON parkings(is_published,is_closed);
CREATE INDEX IF NOT EXISTS idx_submissions_state ON submissions(status,created_at);
CREATE INDEX IF NOT EXISTS idx_points_user ON point_transactions(user_id);
'''

def init_db():
    con=get_db()
    try:
        con.executescript(SCHEMA)
        if not con.execute('SELECT 1 FROM settings WHERE key="redeem_enabled"').fetchone():
            con.execute('INSERT INTO settings(key,value) VALUES (?,?)',('redeem_enabled','false'))
        con.commit()
    finally:
        con.close()

def setting(key, default=''):
    row=query('SELECT value FROM settings WHERE key=?',(key,),True)
    return row['value'] if row else default

def balance(con, user_id):
    result=con.execute('SELECT COALESCE(SUM(amount),0) AS b FROM point_transactions WHERE user_id=?',(user_id,)).fetchone()
    return result['b']

def audit(con, actor, action, target, detail=''):
    con.execute('INSERT INTO audit_logs(actor_id,action,target,detail,created_at) VALUES (?,?,?,?,?)',
        (actor,action,str(target),str(detail)[:1000],utcnow()))

def seed_file(path=None):
    source=Path(path) if path else ROOT / 'data' / 'test_parking_12.json'
    items=json.loads(source.read_text('utf-8'))
    n=0
    with transaction() as con:
        for p in items:
            if con.execute('SELECT 1 FROM parkings WHERE external_code=?',(p['id'],)).fetchone():
                continue
            con.execute('''INSERT INTO parkings (external_code,name,operator,address,lat,lng,entrance_confirmed,hours,capacity,pricing_description,payment_methods,notes,source_url,source_checked_at,onsite_checked_at,created_at,updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                p['id'],p['name'],p.get('operator',''),p['address'],p.get('latitude'),p.get('longitude'),0,
                p.get('open_hours',''),p.get('capacity'),p.get('price_details',''),p.get('payment_methods',''),
                'テストデータ・現地未確認。正確な入口位置と料金の再確認が必要。',p.get('source_url',''),p.get('source_access_date'),p.get('on_site_check_date'),utcnow(),utcnow()))
            pid=con.execute('SELECT last_insert_rowid()').fetchone()[0]
            wd_m=p.get('weekday_unit_min'); wd_y=p.get('weekday_unit_yen')
            wk_m=p.get('weekend_unit_min'); wk_y=p.get('weekend_unit_yen')
            caps=[]
            if p.get('weekday_max_yen') and p.get('max_window_hours'):
                caps.append({'type':'duration','hours':p['max_window_hours'],'yen':p['weekday_max_yen'],'weekdays':[0,1,2,3,4],'repeat':False})
            if p.get('weekend_max_yen') and p.get('max_window_hours'):
                caps.append({'type':'duration','hours':p['max_window_hours'],'yen':p['weekend_max_yen'],'weekdays':[5,6],'repeat':False})
            # Source summaries can include overlapping night caps and ambiguous repetition.
            # Avoid misleading automated charges until rules are checked by an admin.
            conf={'weekday':{'minutes':wd_m,'yen':wd_y},'weekend':{'minutes':wk_m,'yen':wk_y},
                  'periods':[],'caps':caps,'verified_for_calculation':False}
            con.execute('INSERT INTO rate_rules(parking_id,config_json,updated_at) VALUES (?,?,?)',
                        (pid,json.dumps(conf,ensure_ascii=False),utcnow()))
            n+=1
    return n
