"""Deterministic indicators from collected closed bars. Models interpret these; they never compute them."""
import math

PERIODS = (20, 50, 200)
RSI_PERIOD = 14
ATR_PERIOD = 14
VOLUME_WINDOW = 20


def sig(value, digits=8):
    """Round to significant digits so prices of any scale stay readable."""
    return float(f'{value:.{digits}g}')


def sma(values, period):
    return sum(values[-period:]) / period if len(values) >= period else None


def rsi(closes, period=RSI_PERIOD):
    # Wilder smoothing, the convention charting tools use.
    if len(closes) <= period:
        return None
    diffs = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(d, 0) for d in diffs[:period]) / period
    loss = sum(max(-d, 0) for d in diffs[:period]) / period
    for d in diffs[period:]:
        gain = (gain * (period - 1) + max(d, 0)) / period
        loss = (loss * (period - 1) + max(-d, 0)) / period
    if loss == 0:
        return 50.0 if gain == 0 else 100.0
    return 100 - 100 / (1 + gain / loss)


def atr(highs, lows, closes, period=ATR_PERIOD):
    if len(closes) <= period:
        return None
    ranges = [max(h - low, abs(h - prev), abs(low - prev))
              for h, low, prev in zip(highs[1:], lows[1:], closes)]
    value = sum(ranges[:period]) / period
    for r in ranges[period:]:
        value = (value * (period - 1) + r) / period
    return value


def interval_indicators(bars):
    closes = [float(b['close']) for b in bars]
    highs = [float(b['high']) for b in bars]
    lows = [float(b['low']) for b in bars]
    volumes = [float(b['volume']) for b in bars]
    close = closes[-1]
    result = {'bars': len(bars), 'close': sig(close)}
    for period in PERIODS:
        average = sma(closes, period)
        if average:
            result[f'sma{period}'] = sig(average)
            result[f'closeVsSma{period}Pct'] = round((close / average - 1) * 100, 3)
    value = rsi(closes)
    if value is not None:
        result['rsi14'] = round(value, 2)
    value = atr(highs, lows, closes)
    if value is not None:
        result['atr14'] = sig(value)
        result['atr14Pct'] = round(value / close * 100, 3)
    for lookback in (1, 20):
        if len(closes) > lookback:
            result[f'change{lookback}Pct'] = round((close / closes[-1 - lookback] - 1) * 100, 3)
    if len(bars) >= VOLUME_WINDOW:
        result['high20'] = sig(max(highs[-VOLUME_WINDOW:]))
        result['low20'] = sig(min(lows[-VOLUME_WINDOW:]))
    if len(volumes) > VOLUME_WINDOW:
        average = sum(volumes[-VOLUME_WINDOW - 1:-1]) / VOLUME_WINDOW
        if average > 0:
            result['volumeRatio20'] = round(volumes[-1] / average, 3)
    return result


def market_indicators(snapshot):
    mark = snapshot['mark']
    index = float(mark['indexPrice'])
    result = {
        'lastPrice': sig(float(snapshot['ticker']['price'])),
        'markPrice': sig(float(mark['markPrice'])),
        'indexPrice': sig(index),
        'fundingRatePct': round(float(mark['lastFundingRate']) * 100, 5),
        'openInterest': sig(float(snapshot['openInterest']['openInterest'])),
    }
    if index > 0:
        result['premiumPct'] = round((float(mark['markPrice']) / index - 1) * 100, 4)
    return result


def pct_change(new, old):
    return round((new / old - 1) * 100, 3) if old else None


def derivatives_indicators(history):
    """Positioning changes; period keys share interval prefixes so horizon scoping still applies."""
    rates = [float(r['fundingRate']) * 100 for r in history['funding']]
    market = {'fundingAvgPct': round(sum(rates) / len(rates), 5), 'fundingMinPct': round(min(rates), 5),
              'fundingMaxPct': round(max(rates), 5), 'fundingPositiveShare': round(sum(r > 0 for r in rates) / len(rates), 3)}
    intervals = {}
    for period, metrics in history['periods'].items():
        values = {}
        oi = [float(r['sumOpenInterest']) for r in metrics['openInterest']]
        for lookback in (1, 24):
            if len(oi) > lookback:
                values[f'oiChange{lookback}Pct'] = pct_change(oi[-1], oi[-1 - lookback])
        values['oiValueUsd'] = sig(float(metrics['openInterest'][-1]['sumOpenInterestValue']), 6)
        for metric, prefix in (('globalLongShort', 'longShortRatio'), ('topLongShort', 'topLongShortRatio')):
            ratios = [float(r['longShortRatio']) for r in metrics[metric]]
            values[prefix] = round(ratios[-1], 4)
            if len(ratios) > 24:
                values[f'{prefix}Change24'] = round(ratios[-1] - ratios[-25], 4)
        taker = [float(r['buySellRatio']) for r in metrics['taker']]
        values['takerBuySellRatio'] = round(taker[-1], 4)
        values['takerBuySellAvg24'] = round(sum(taker[-24:]) / len(taker[-24:]), 4)
        intervals[period] = {k: v for k, v in values.items() if v is not None}
    return {'market': market, 'intervals': intervals}


def compute(snapshot):
    result = {'intervals': {interval: interval_indicators(bars) for interval, bars in snapshot['candles'].items()},
              'market': market_indicators(snapshot)}
    if snapshot.get('derivatives'):
        extra = derivatives_indicators(snapshot['derivatives'])
        result['market'].update(extra['market'])
        for period, values in extra['intervals'].items():
            result['intervals'].setdefault(period, {}).update(values)
    return result


def flatten(indicators):
    """Flat `interval.name` keys the models cite and the server checks."""
    facts = {f'market.{k}': v for k, v in indicators['market'].items()}
    for interval, values in indicators['intervals'].items():
        facts.update({f'{interval}.{k}': v for k, v in values.items() if k != 'bars'})
    if any(not math.isfinite(v) for v in facts.values()):
        raise ValueError('지표 계산 결과가 유한한 숫자가 아닙니다.')
    return facts


def describe(facts, keys):
    """Plain-language direction of each fact, so models do not have to infer above/below or up/down."""
    lines = []
    for key in keys:
        frame, name = key.split('.', 1)
        value = facts[key]
        if name.startswith('closeVsSma'):
            period = name[len('closeVsSma'):-len('Pct')]
            lines.append(f"{frame} 종가는 SMA{period}보다 {abs(value)}% {'위' if value > 0 else '아래' if value < 0 else '같은 위치'}")
        elif name == 'rsi14':
            band = '과매수(70 이상)' if value >= 70 else '과매도(30 이하)' if value <= 30 else '중립(30~70)'
            lines.append(f'{frame} RSI14 {value}: {band}')
        elif name in ('change1Pct', 'change20Pct', 'oiChange1Pct', 'oiChange24Pct'):
            what = '미결제약정' if name.startswith('oi') else '가격'
            span = '1기간' if '1Pct' in name else ('24기간' if name.startswith('oi') else '20기간')
            lines.append(f"{frame} {what} {span} 변화 {value}%: {'증가' if value > 0 else '감소' if value < 0 else '변화 없음'}")
        elif name == 'volumeRatio20':
            level = '평소보다 많음' if value >= 1.5 else '평소보다 적음' if value <= 0.7 else '평소 수준'
            lines.append(f'{frame} 거래량은 직전 20봉 평균의 {value}배: {level}')
        elif name in ('longShortRatioChange24', 'topLongShortRatioChange24'):
            who = '전체 계정' if name.startswith('long') else '대형 트레이더'
            lines.append(f"{frame} {who} 롱숏 비율 24기간 변화 {value}: {'롱 쪽으로 이동' if value > 0 else '숏 쪽으로 이동' if value < 0 else '변화 없음'}")
        elif name in ('longShortRatio', 'topLongShortRatio'):
            who = '전체 계정' if name == 'longShortRatio' else '대형 트레이더'
            lines.append(f"{frame} {who} 롱숏 비율 {value}: {'롱이 더 많음' if value > 1 else '숏이 더 많음' if value < 1 else '같음'}")
        elif name == 'takerBuySellRatio':
            average = facts.get(f'{frame}.takerBuySellAvg24')
            side = '시장가 매수 우위' if value > 1 else '시장가 매도 우위' if value < 1 else '균형'
            versus = '' if average is None else f", 24기간 평균 {average}보다 {'높음' if value > average else '낮음' if value < average else '같음'}"
            lines.append(f'{frame} 테이커 매수/매도 비율 {value}: {side}{versus}')
        elif name == 'fundingRatePct':
            lines.append(f"현재 펀딩비율 {value}%: {'롱이 숏에 지불' if value > 0 else '숏이 롱에 지불' if value < 0 else '0'}")
        elif name == 'premiumPct':
            lines.append(f"마크 가격은 인덱스보다 {abs(value)}% {'높음' if value > 0 else '낮음' if value < 0 else '같음'}")
        elif frame == 'macro':
            lines.append(describe_macro(name, value))
    return lines


MACRO_NAMES = {'vix': 'VIX', 'spx': 'S&P500', 'ndx': '나스닥100', 'us2y': '미국 2년 금리', 'us10y': '미국 10년 금리'}


def describe_macro(name, value):
    direction = '상승' if value > 0 else '하락' if value < 0 else '변화 없음'
    if name == 'vix':
        level = '높음(25 이상)' if value >= 25 else '낮음(15 이하)' if value <= 15 else '보통(15~25)'
        return f'VIX {value}: 변동성 경계 {level}'
    if name == 'curve2s10sBp':
        return f"미국 10년-2년 금리차 {value}bp: {'정상(10년이 높음)' if value > 0 else '역전(2년이 높음)' if value < 0 else '같음'}"
    for prefix, label in MACRO_NAMES.items():
        if name.startswith(prefix):
            rest = name[len(prefix):]
            if rest == 'Pct':
                return f'{label} {value}%'
            span = '1일' if '1d' in rest else '5일'
            unit = 'bp' if rest.endswith('Bp') else '%'
            return f'{label} {span} 변화 {value}{unit}: {direction}'
    return f'{name} {value}'
