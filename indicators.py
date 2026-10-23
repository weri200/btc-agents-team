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


def compute(snapshot):
    return {'intervals': {interval: interval_indicators(bars) for interval, bars in snapshot['candles'].items()},
            'market': market_indicators(snapshot)}


def flatten(indicators):
    """Flat `interval.name` keys the models cite and the server checks."""
    facts = {f'market.{k}': v for k, v in indicators['market'].items()}
    for interval, values in indicators['intervals'].items():
        facts.update({f'{interval}.{k}': v for k, v in values.items() if k != 'bars'})
    if any(not math.isfinite(v) for v in facts.values()):
        raise ValueError('지표 계산 결과가 유한한 숫자가 아닙니다.')
    return facts
