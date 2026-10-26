from fastapi.testclient import TestClient
from app.main import app
from app.db import query


def test_public_demo_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'parking.sqlite3'))
    monkeypatch.setenv('DEMO_MODE','true')
    monkeypatch.setenv('ENVIRONMENT','development')
    with TestClient(app) as c:
        assert c.get('/healthz').json() == {'status':'ok'}
        page = c.get('/')
        assert page.status_code == 200
        assert '公開プレビュー' in page.text
        assert 'タイムズ' in page.text
        assert len(query('SELECT id FROM parkings')) == 12
        assert c.post('/register', data={}).status_code == 403
        assert c.post('/contribute', data={}).status_code == 403


def test_read_only_demo_cannot_edit_ad_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'parking.sqlite3'))
    monkeypatch.setenv('DEMO_MODE','true')
    monkeypatch.setenv('ENVIRONMENT','development')
    with TestClient(app) as c:
        assert c.post('/admin/ads', data={}).status_code == 403
