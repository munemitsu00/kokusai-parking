from datetime import datetime
from app.pricing import estimate

BASE={'weekday':{'minutes':30,'yen':200},'weekend':{'minutes':30,'yen':200},'periods':[],'caps':[],'verified_for_calculation':True}

def test_standard_rate():
    assert estimate('2026-10-26T10:00','2026-10-26T12:00',BASE)['amount']==800
    assert estimate('2026-10-26T10:00','2026-10-26T10:01',BASE)['amount']==200

def test_24_hour_repeat_cap():
    conf={**BASE,'caps':[{'type':'duration','hours':24,'yen':900,'repeat':True}]}
    assert estimate('2026-10-26T10:00','2026-10-27T10:00',conf)['amount']==900
    assert estimate('2026-10-26T10:00','2026-10-28T10:00',conf)['amount']==1800

def test_24_hour_no_repeat():
    conf={**BASE,'caps':[{'type':'duration','hours':24,'yen':900,'repeat':False}]}
    assert estimate('2026-10-26T10:00','2026-10-28T10:00',conf)['amount']==900+9600

def test_daytime_cap_until_20():
    conf={**BASE,'caps':[{'type':'daypart','start':'08:00','end':'20:00','yen':700,'repeat':True}]}
    assert estimate('2026-10-26T09:00','2026-10-26T19:00',conf)['amount']==700
    assert estimate('2026-10-26T19:50','2026-10-26T20:10',conf)['amount']==400

def test_night_cap():
    conf={**BASE,'caps':[{'type':'daypart','start':'20:00','end':'08:00','yen':500,'repeat':True}]}
    assert estimate('2026-10-26T20:00','2026-10-27T08:00',conf)['amount']==500

def test_daynight_independent_caps():
    conf={**BASE,'caps':[{'type':'daypart','start':'08:00','end':'20:00','yen':900},
                          {'type':'daypart','start':'20:00','end':'08:00','yen':400}]}
    assert estimate('2026-10-26T08:00','2026-10-27T08:00',conf)['amount']==1300

def test_overlapping_caps_safely_unknown():
    conf={**BASE,'caps':[{'type':'duration','hours':24,'yen':800},
                          {'type':'daypart','start':'20:00','end':'08:00','yen':400}]}
    result=estimate('2026-10-26T18:00','2026-10-27T09:00',conf)
    assert result['amount'] is None and '重複' in result['reason']

def test_unverified_unknown():
    assert estimate('2026-10-26T09:00','2026-10-26T10:00',{**BASE,'verified_for_calculation':False})['amount'] is None

def test_max_duration():
    assert estimate('2026-10-26T09:00','2026-11-05T10:00',BASE)['amount'] is None
