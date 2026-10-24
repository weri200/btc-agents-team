"""4h rule research: candidate rules decide at past 4h closes, and each trade is scored by whether its target or its stop
is hit first on 1h bars. No models run, so months of moments take minutes. Run: python research.py --symbol BTCUSDT --days 180
Facts come from the same server indicators the models see (indicators.compute), so a rule found here can be handed to them."""
import argparse
from bisect import bisect_left
import itertools
import time

import backtest
import indicators
import market
from market import BAR_COUNT, INTERVALS, MarketError

H = 3600000
DAY = 24 * H
STEP = INTERVALS['4h']
FRAMES = ('1h', '4h', '1d')
BAR_FIELDS = ('openTime', 'open', 'high', 'low', 'close', 'volume', 'closeTime', 'quoteVolume', 'trades', 'takerBuyVolume', 'takerBuyQuoteVolume')


def klines(collector, symbol, interval, start, end, now):
    """Bars opening in [start, end) and closed by `now`, 1500 per request. Exchange outages leave gaps; bars never repeat or go back."""
    step, bars, at = INTERVALS[interval], [], start
    while at < end:
        rows = collector.request('/fapi/v1/klines', symbol=symbol, interval=interval, startTime=at, endTime=end - 1, limit=1500)
        if not rows:
            break
        for row in rows:
            opened, closed = market.timestamp(row[0]), market.timestamp(row[6])
            if opened < at or opened % step or closed != opened + step - 1:
                raise MarketError(f'{interval} 캔들 중복·순서 오류 ({market.iso(opened)})')
            o, h, low, c = [market.number(value, positive=True) for value in row[1:5]]
            if not low <= min(o, c) <= max(o, c) <= h:
                raise MarketError(f'{interval} 캔들 OHLC 범위 오류 ({market.iso(opened)})')
            if closed >= now:
                return bars
            bars.append(dict(zip(BAR_FIELDS, row[:11])))
            at = opened + step
    return bars


def funding_history(collector, symbol, start, end):
    rows, at = [], start
    while at < end:
        page = collector.request('/fapi/v1/fundingRate', symbol=symbol, startTime=at, endTime=end - 1, limit=1000)
        if not page:
            break
        rows += market.history_rows(page, symbol, ('fundingRate',), 'fundingTime', signed=('fundingRate',))
        at = rows[-1]['time'] + 1
    return rows


def facts_at(symbol, at, bars, closes, funding, folder):
    """The server facts as they stood just before `at`, plus the 4h entry close."""
    candles = {i: bars[i][max(0, n - BAR_COUNT):n] for i in FRAMES for n in [bisect_left(closes[i], at)]}
    if any(not c or c[-1]['closeTime'] != at - 1 for i, c in candles.items() if i != '1d'):
        raise MarketError(f'{market.iso(at)} 직전 캔들이 없습니다.')
    settled = [r for r in funding if r['time'] < at][-market.FUNDING_COUNT:]
    if not settled:
        raise MarketError(f'{market.iso(at)} 이전 펀딩 기록이 없습니다.')
    price = candles['1h'][-1]['close']
    snapshot = {'candles': candles, 'ticker': {'price': price},
                'mark': {'markPrice': price, 'indexPrice': price, 'lastFundingRate': settled[-1]['fundingRate']},
                'openInterest': {'openInterest': backtest.metrics_at(symbol, at - INTERVALS['5m'], folder)['sum_open_interest']},
                'derivatives': backtest.derivatives_at(symbol, at, candles, settled, folder)}
    return indicators.flatten(indicators.compute(snapshot)), float(candles['4h'][-1]['close'])


def side(*values):
    """+1 or -1 when every value is on that side of zero, otherwise 0."""
    if all(v is not None and v > 0 for v in values):
        return 1
    if all(v is not None and v < 0 for v in values):
        return -1
    return 0


def trend(f):
    return side(f.get('1d.change20Pct'), f.get('1d.closeVsSma50Pct'))


def momentum(f):
    t = trend(f)
    return t if t and side(f.get('4h.closeVsSma20Pct'), f.get('4h.change1Pct')) == t else 0


def breakout(f):
    t = trend(f)
    return t if (t == 1 and f['4h.close'] > f.get('4h.high20', float('inf'))) or (t == -1 and f['4h.close'] < f.get('4h.low20', 0)) else 0


def pullback(f):
    t, rsi = trend(f), f.get('4h.rsi14', 50)
    return t if (t == 1 and rsi < 45) or (t == -1 and rsi > 55) else 0


# Each rule reads the facts before a 4h close and returns 1 (long), -1 (short) or 0 (no trade).
RULES = {
    'trend': ('1일 추세 방향', trend),
    'trend-200': ('1일 추세 + SMA200 (현재 스윙 규칙)', lambda f: side(f.get('1d.change20Pct'), f.get('1d.closeVsSma50Pct'), f.get('1d.closeVsSma200Pct'))),
    'momentum': ('1일 추세 + 4시간 모멘텀', momentum),
    'momentum-oi': ('1일 추세 + 4시간 모멘텀 + 미결제약정 증가', lambda f: momentum(f) if f.get('4h.oiChange1Pct', 0) > 0 else 0),
    'momentum-funding': ('1일 추세 + 4시간 모멘텀, 같은 쪽 펀딩 과열 제외', lambda f: momentum(f) if momentum(f) * f['market.fundingRatePct'] < 0.02 else 0),
    'breakout': ('1일 추세 + 4시간 20봉 돌파', breakout),
    'breakout-volume': ('1일 추세 + 4시간 20봉 돌파 + 거래량 증가', lambda f: breakout(f) if f.get('4h.volumeRatio20', 0) > 1 else 0),
    'pullback': ('1일 추세 + 4시간 RSI 눌림', pullback),
}


def trade(direction, entry, atr, path, tp, sl, hold, fee):
    """Net return of one trade: exit at the target or the stop, whichever 1h bar reaches first, else at the close after `hold` bars.
    A bar that touches both counts as the stop, since 1h bars cannot tell which came first."""
    target, stop = entry + direction * tp * atr, entry - direction * sl * atr
    exit_ = float(path[hold - 1]['close'])
    for bar in path[:hold]:
        high, low = float(bar['high']), float(bar['low'])
        if (low <= stop) if direction > 0 else (high >= stop):
            exit_ = stop
            break
        if (high >= target) if direction > 0 else (low <= target):
            exit_ = target
            break
    return (exit_ / entry - 1) * direction - fee


def research(symbol, days, tps, sls, holds, fee_pct, collector=None, now=None, folder=backtest.ARCHIVE, log=print):
    """Score every rule and exit setting on the 4h closes of the last `days`; the first two thirds are 'train', the rest 'test'."""
    # shortcut: signals every 4h with holds longer than 4h overlap, so trades are not independent; fine for comparing rules,
    # add one-position-at-a-time simulation before reading the numbers as account returns.
    collector = collector or market.Collector()
    now = now or int(time.time() * 1000)
    longest = max(holds) * H
    last = (now - longest) // STEP * STEP
    moments = list(range(last - (days * DAY) // STEP * STEP + STEP, last + 1, STEP))
    log(f'{symbol} · 4시간봉 {len(moments)}개 시점 · 캔들과 아카이브를 받는 중')
    bars = {i: klines(collector, symbol, i, moments[0] - BAR_COUNT * INTERVALS[i], moments[-1] + longest + INTERVALS[i], now) for i in FRAMES}
    closes = {i: [int(b['closeTime']) for b in bars[i]] for i in FRAMES}
    opens_1h = [int(b['openTime']) for b in bars['1h']]
    funding = funding_history(collector, symbol, moments[0] - 10 * DAY, moments[-1])
    split = moments[len(moments) * 2 // 3]
    groups, skipped = {}, 0
    for at in moments:
        try:
            facts, entry = facts_at(symbol, at, bars, closes, funding, folder)
        except MarketError as exc:
            skipped += 1
            log(f'{market.iso(at)} 건너뜀: {exc}')
            continue
        start = bisect_left(opens_1h, at)
        path = bars['1h'][start:start + max(holds)]
        part = 'train' if at < split else 'test'
        for rule, (_, decide) in RULES.items():
            direction = decide(facts)
            for tp, sl, hold in itertools.product(tps, sls, holds):
                g = groups.setdefault((rule, tp, sl, hold), {'rule': rule, 'tp': tp, 'sl': sl, 'holdHours': hold,
                                                             'train': [], 'test': [], 'longs': 0, 'shorts': 0})
                if not direction or len(path) < hold or int(path[hold - 1]['openTime']) != at + (hold - 1) * H:
                    continue  # no trade, or a gap in the 1h bars after entry
                g[part].append(trade(direction, entry, facts['4h.atr14'], path, tp, sl, hold, fee_pct / 100))
                g['longs' if direction > 0 else 'shorts'] += 1
    results = []
    for g in groups.values():
        for part in ('train', 'test'):
            nets = g.pop(part)
            g[part] = {'trades': len(nets), 'hitRate': sum(n > 0 for n in nets) / len(nets) if nets else None,
                       'meanNetPct': sum(nets) / len(nets) * 100 if nets else None}
        results.append(g)
    return {'moments': len(moments), 'skipped': skipped, 'split': split, 'results': results}


def report(data, min_trades, top, log=print):
    log(f"시점 {data['moments']}개 (건너뜀 {data['skipped']}) · 학습 구간 ~ {market.iso(data['split'])[:10]} · 검증 구간 그 이후")
    log('규칙 | 목표·손절(ATR배)·보유 | 학습: 진입·적중률·평균순수익 | 검증: 진입·적중률·평균순수익')
    fmt = lambda p: f"{p['trades']:4d} · {'—' if p['hitRate'] is None else format(p['hitRate'], '4.0%')} · {'—' if p['meanNetPct'] is None else format(p['meanNetPct'], '+.2f') + '%'}"
    ranked = sorted(data['results'], key=lambda g: (g['train']['trades'] >= min_trades, g['train']['hitRate'] or 0), reverse=True)
    for g in ranked[:top]:
        flag = '' if g['train']['trades'] >= min_trades else '  (진입 부족)'
        log(f"{RULES[g['rule']][0]} | {g['tp']}·{g['sl']}·{g['holdHours']}h | {fmt(g['train'])} | {fmt(g['test'])}{flag}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='4시간봉 규칙 후보를 목표가·손절 중 먼저 닿는 쪽으로 채점합니다. 모델은 실행하지 않습니다.')
    parser.add_argument('--symbol', default='BTCUSDT', choices=market.SYMBOLS)
    parser.add_argument('--days', type=int, default=180, help='오늘부터 거슬러 올라갈 일수')
    parser.add_argument('--tp', type=float, nargs='+', default=[1.0, 1.5, 2.0], help='목표가: 4시간 ATR의 몇 배')
    parser.add_argument('--sl', type=float, nargs='+', default=[1.0, 1.5], help='손절: 4시간 ATR의 몇 배')
    parser.add_argument('--hold-hours', type=int, nargs='+', default=[12, 24], help='최대 보유 시간')
    parser.add_argument('--fee', type=float, default=0.1, help='왕복 수수료(%%), 기본 테이커 0.05%% x 2')
    parser.add_argument('--min-trades', type=int, default=30, help='학습 구간 최소 진입 수')
    parser.add_argument('--top', type=int, default=30, help='표시할 줄 수')
    args = parser.parse_args()
    if args.days < 7 or min(args.tp + args.sl) <= 0 or min(args.hold_hours) <= 0:
        parser.error('--days는 7 이상, 목표·손절·보유 시간은 0보다 커야 합니다.')
    report(research(args.symbol, args.days, args.tp, args.sl, args.hold_hours, args.fee), args.min_trades, args.top)
