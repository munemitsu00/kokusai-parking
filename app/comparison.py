"""Conservative public comparison for web-sourced *trial* tariff summaries.

Only show calculations when the complete standard tariff and an unambiguous
short-stay cap are present. Does not infer entrance positions or pricing rules.
Never treat a web-source estimate as onsite verified.
"""
from datetime import datetime
from math import ceil
from .pricing import estimate, MAX_MINUTES

# Web summaries for these source codes have a single duration cap without
# conflicting day/night caps. Other sample facilities remain '要確認'.
SIMPLE_SOURCE_CODES = frozenset({'T001', 'T004', 'T005', 'T006', 'R002', 'R003', 'R005'})

# Coordinates intentionally indicate only a *neighborhood*, not a parking lot
# or its vehicle entrance. Never use them as navigation endpoints.
AREA_REFERENCE = {
    'T001': (26.2164, 127.6896),  # 牧志2丁目周辺
    'T002': (26.2129, 127.6812),  # 松尾1丁目周辺
    'T003': (26.2141, 127.6826),
    'T004': (26.2154, 127.6807),  # 久茂地3丁目周辺
    'T005': (26.2168, 127.6868),  # 牧志1丁目周辺
    'T006': (26.2151, 127.6867),  # 松尾2丁目周辺
    'T007': (26.2139, 127.6835),
    'R001': (26.2139, 127.6820),
    'R002': (26.2165, 127.6893),
    'R003': (26.2167, 127.6858),
    'R004': (26.2170, 127.6865),
    'R005': (26.2155, 127.6844),
}


def reference_position(p):
    if p.get('lat') is not None and p.get('lng') is not None:
        return p['lat'], p['lng'], not bool(p.get('entrance_confirmed'))
    position = AREA_REFERENCE.get(p.get('external_code'))
    if position:
        return position[0], position[1], True
    return None, None, True


def public_estimate(p, cfg, arrive, leave):
    """Return amount/label/reason; never silently assume unprovided tariffs."""
    if not arrive or not leave:
        return {'amount': None, 'reason': '日時を指定してください', 'is_reference': False}
    # Admin-verified full rule set, not necessarily onsite-confirmed.
    if cfg.get('verified_for_calculation'):
        output = estimate(arrive, leave, cfg)
        return {**output, 'is_reference': not bool(p.get('onsite_checked_at'))}
    if p.get('external_code') not in SIMPLE_SOURCE_CODES:
        return {'amount': None, 'reason': '時間帯別料金や最大料金の詳細を確認中です', 'is_reference': True}
    try:
        start = datetime.fromisoformat(arrive)
        finish = datetime.fromisoformat(leave)
        if start.tzinfo or finish.tzinfo:
            raise ValueError('日時は日本時間で入力してください')
        minutes = int((finish-start).total_seconds() / 60)
        if not 0 < minutes <= MAX_MINUTES:
            raise ValueError('1分〜7日以内の駐車時間を指定してください')
        # Safeguard: current source data cannot establish holiday or cross-day
        # maximum-charge rules reliably. Do not rank those sessions.
        if start.date() != finish.date():
            raise ValueError('日付をまたぐ料金はまだ確認できません')
        if start.weekday() != finish.weekday():
            raise ValueError('曜日をまたぐ料金はまだ確認できません')
        cap = cfg.get('caps', [])
        if len(cap) not in (1, 2) or any(x.get('type') != 'duration' for x in cap):
            raise ValueError('最大料金の条件が未確認です')
        applicable = [c for c in cap if start.weekday() in c.get('weekdays', list(range(7)))]
        if len(applicable) != 1:
            raise ValueError('料金の適用条件が確認できません')
        matched = applicable[0]
        if minutes > float(matched['hours']) * 60:
            raise ValueError('最大料金の適用時間を超えるため料金要確認')
        rate = cfg.get('weekend' if start.weekday() >= 5 else 'weekday')
        if not rate or not rate.get('minutes') or rate.get('yen') is None:
            raise ValueError('通常料金の設定がありません')
        regular = ceil(minutes / int(rate['minutes'])) * int(rate['yen'])
        amount = min(regular, int(matched['yen']))
        return {'amount': amount, 'reason': None, 'is_reference': True,
                'breakdown': [{'label': '公式サイト等の公開情報に基づく暫定概算（現地未確認）', 'yen': amount}]}
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {'amount': None, 'reason': str(exc), 'is_reference': True}
