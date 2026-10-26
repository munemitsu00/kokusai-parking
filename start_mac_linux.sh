#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py init
python manage.py seed
echo 'サイト: http://127.0.0.1:8000'
echo '管理者登録: .venv/bin/python manage.py admin'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
