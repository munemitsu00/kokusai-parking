import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db, seed_file, query
from app.comparison import reference_position, public_estimate


def test_all_sample_locations_are_marked_approximate():
    samples = json.loads((Path(__file__).resolve().parents[1] / 'data/test_parking_12.json').read_text())
    assert len(samples) == 12
    for entry in samples:
        p = {'external_code': entry['id'], 'lat':None, 'lng':None, 'entrance_confirmed':False}
        lat,lng,approx = reference_position(p)
        assert 26.20 < lat < 26.23 and 127.67 < lng < 127.70
        assert approx is True


def test_fee_comparison_date_restrictions(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'test.sqlite3'))
    monkeypatch.setenv('DEMO_MODE','true')
    monkeypatch.setenv('ENVIRONMENT', 'development')
    with TestClient(app) as client:
        r = client.get('/?arrive=2026-10-26T10%3A00&leave=2026-10-26T12%3A00')
        assert r.status_code == 200
        assert '入庫日時' in r.text and '料金が安い順' in r.text
        assert '参考料金' in r.text
        assert '料金要確認' in r.text
        assert 'window.PARKING_MARKERS' in r.text
        data = json.loads(r.text.split('window.PARKING_MARKERS = ',1)[1].split(';',1)[0])
        assert len(data) == 12
        assert all(x['approx'] for x in data)
        assert any(x['price'] is not None for x in data)
        assert any(x['price'] is None for x in data)
        # Two-hour standard weekday prices; no assumptions about complex caps.
        t001=next(x for x in data if x['name']=='タイムズ沖縄牧志')
        assert t001['price']==600
        assert '料金が安い順' in r.text
        invalid = client.get('/?arrive=2026-10-26T12:00&leave=2026-10-26T10:00')
        assert invalid.status_code == 200 and '日時を正しく' in invalid.text
        crossday=client.get('/?arrive=2026-10-26T23:00&leave=2026-10-27T01:00')
        assert crossday.status_code == 200 and '日付をまたぐ料金' in crossday.text


def test_unverified_unknown_rates_never_ranked():
    p={'external_code':'R001','onsite_checked_at':None}
    cfg={'weekday':{'minutes':10,'yen':200},'weekend':{'minutes':10,'yen':200},'caps':[], 'verified_for_calculation':False}
    res=public_estimate(p,cfg,'2026-10-26T10:00','2026-10-26T12:00')
    assert res['amount'] is None
