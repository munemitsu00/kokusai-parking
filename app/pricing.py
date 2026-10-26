"""Conservative rule-based parking charge estimator.

Never claims accuracy on unverified, inconsistent, or overlapping cap schemes.
All datetimes are Asia/Tokyo local and naive, with no DST in Japan.
"""
from datetime import datetime, timedelta, time, date
import json
import math

MAX_MINUTES = 7 * 24 * 60

def validate_config(cfg):
    if not isinstance(cfg,dict): raise ValueError('料金設定はJSONオブジェクトにしてください')
    for key in ('weekday','weekend'):
        rate=cfg.get(key,{})
        if rate and (not isinstance(rate.get('minutes'),int) or rate['minutes'] < 1 or rate['minutes'] > 1440 or not isinstance(rate.get('yen'),int) or rate['yen'] < 0):
            raise ValueError(f'{key}: 課金単位と料金を確認してください')
    for item in cfg.get('periods',[]):
        _clock(item['start']); _clock(item['end'])
        if not 1<=int(item['minutes'])<=1440 or int(item['yen'])<0: raise ValueError('時間帯料金の値が不正です')
    for cap in cfg.get('caps',[]):
        if cap.get('type') not in ('duration','daypart','calendar_day'): raise ValueError('最大料金の種類が不正です')
        if type(cap.get('yen')) is not int or cap['yen']<0: raise ValueError('最大料金は0円以上の整数です')
        if cap['type']=='duration' and not 1 <= float(cap.get('hours',0)) <= 168: raise ValueError('最大料金の適用時間が不正です')
        if cap['type']=='daypart':
            _clock(cap['start']); _clock(cap['end'])
        w=cap.get('weekdays',list(range(7)))
        if any(x not in range(7) for x in w): raise ValueError('曜日設定が不正です')
    return cfg

def _clock(s):
    hh,mm=map(int,str(s).split(':'))
    if hh not in range(24) or mm not in range(60): raise ValueError('時刻は HH:MM で指定してください')
    return hh*60+mm

def _period_match(dt,period):
    minute=dt.hour*60+dt.minute
    start=_clock(period['start']); end=_clock(period['end'])
    if start==end: return False
    if start<end:
        return start<=minute<end and dt.weekday() in period.get('weekdays',list(range(7)))
    effective_date=dt if minute>=start else dt-timedelta(days=1)
    return (minute>=start or minute<end) and effective_date.weekday() in period.get('weekdays',list(range(7)))

def _rate_at(dt,cfg):
    special=cfg.get('special_dates',{}).get(dt.date().isoformat())
    if special:
        return int(special['minutes']),int(special['yen'])
    for period in cfg.get('periods',[]):
        if _period_match(dt,period): return int(period['minutes']),int(period['yen'])
    key='weekend' if dt.weekday()>=5 else 'weekday'
    r=cfg.get(key) or cfg.get('weekday')
    if not r or not r.get('minutes') or r.get('yen') is None:
        raise ValueError('通常料金が未設定です')
    return int(r['minutes']),int(r['yen'])

def _base_charge(start,end,cfg):
    # For each continuous stretch at a given unit rate, round up to whole units.
    # Supports hourly, nighttime and calendar price changes; no carry over at changes.
    cursor=start
    total=0
    while cursor<end:
        unit,yen=_rate_at(cursor,cfg)
        cutoff=min(end,cursor+timedelta(minutes=1))
        while cutoff<end and _rate_at(cutoff,cfg)==(unit,yen):
            cutoff+=timedelta(minutes=1)
        mins=int((cutoff-cursor).total_seconds()//60)
        total+=math.ceil(mins/unit)*yen
        cursor=cutoff
    return total

def _cap_windows(start,end,cfg):
    windows=[]
    for idx,cap in enumerate(cfg.get('caps',[])):
        weekdays=cap.get('weekdays',list(range(7)))
        kind=cap['type']; repeat=cap.get('repeat',True)
        candidates=[]
        if kind=='duration':
            hours=float(cap['hours'])
            current=start
            if start.weekday() in weekdays:
                while current<end:
                    next_=current+timedelta(hours=hours)
                    candidates.append((current,next_))
                    current=next_
                    if not repeat: break
        else:
            day=start.date()-timedelta(days=1)
            last=end.date()+timedelta(days=1)
            while day<=last:
                if day.weekday() in weekdays:
                    dt=datetime.combine(day,time.min)
                    if kind=='calendar_day':
                        left=dt; right=dt+timedelta(days=1)
                    else:
                        begin=_clock(cap['start']); finish=_clock(cap['end'])
                        left=dt+timedelta(minutes=begin)
                        right=dt+timedelta(minutes=finish)
                        if finish<=begin: right+=timedelta(days=1)
                    candidates.append((left,right))
                day+=timedelta(days=1)
            if not repeat:
                candidates=[c for c in candidates if c[1]>start][:1]
        for a,b in candidates:
            x=max(start,a); y=min(end,b)
            if x<y:
                windows.append({'start':x,'end':y,'yen':int(cap['yen']),'index':idx})
    windows.sort(key=lambda x:(x['start'],x['end']))
    for i in range(1,len(windows)):
        if windows[i]['start']<windows[i-1]['end']:
            raise ValueError('最大料金ルールが重複しており、正確に計算できません')
    return windows

def estimate(start,end,cfg):
    try:
        validate_config(cfg)
        if not cfg.get('verified_for_calculation',False):
            return {'amount':None,'reason':'料金ルールが未検証です（現地確認後に管理者が有効化）','breakdown':[]}
        if isinstance(start,str): start=datetime.fromisoformat(start)
        if isinstance(end,str): end=datetime.fromisoformat(end)
        if start.tzinfo or end.tzinfo: raise ValueError('日本時間の日時で入力してください')
        minutes=int((end-start).total_seconds()/60)
        if not 0<minutes<=MAX_MINUTES: raise ValueError('駐車時間は1分〜7日以内で指定してください')
        if cfg.get('free_minutes') and minutes<=int(cfg['free_minutes']):
            return {'amount':0,'reason':None,'breakdown':[{'label':'無料時間内','yen':0}]}
        windows=_cap_windows(start,end,cfg)
        cursor=start
        total=0; breakdown=[]
        for w in windows:
            if cursor<w['start']:
                base=_base_charge(cursor,w['start'],cfg)
                total+=base
                breakdown.append({'label':f'{cursor:%m/%d %H:%M}–{w["start"]:%m/%d %H:%M} 通常料金','yen':base})
            base=_base_charge(w['start'],w['end'],cfg)
            charge=min(base,w['yen'])
            total+=charge
            breakdown.append({'label':f'{w["start"]:%m/%d %H:%M}–{w["end"]:%m/%d %H:%M} 最大料金適用' if charge<base else f'{w["start"]:%m/%d %H:%M}–{w["end"]:%m/%d %H:%M} 通常料金','yen':charge})
            cursor=w['end']
        if cursor<end:
            base=_base_charge(cursor,end,cfg)
            total+=base
            breakdown.append({'label':f'{cursor:%m/%d %H:%M}–{end:%m/%d %H:%M} 通常料金','yen':base})
        return {'amount':total,'reason':None,'breakdown':breakdown,
                'notice':'参考料金です。実際の精算額は駐車場の料金看板・精算機を優先してください。'}
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        return {'amount':None,'reason':str(exc),'breakdown':[]}

DEFAULT_RATE = {'weekday':{'minutes':30,'yen':200},'weekend':{'minutes':30,'yen':200},
                'periods':[],'caps':[],'verified_for_calculation':False}
