"""4h rule research: candidate rules decide at past 4h closes, and each trade is scored by whether its target or its stop
is hit first on 1h bars. No models run, so months of moments take minutes. Run: python research.py --symbol BTCUSDT --days 180
Facts come from the same server indicators the live analysis uses (indicators.compute), so a rule found here can go into signals.py as is."""
import argparse
from bisect import bisect_left
import itertools
import json
from pathlib import Path
import time

import backtest
import indicators
import market
import signals
from signals import breakout, side, trend
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
    facts = {**indicators.flatten(indicators.compute(snapshot)), **bands(candles['4h']), **bands(candles['1h'], prefix='bb1h')}
    n = bisect_left(closes['15m'], at) if '15m' in closes else 0
    if n and bars['15m'][n - 1]['closeTime'] == at - 1:  # a missing 15m bar only leaves out the 15m CCI
        facts.update(bands(bars['15m'][max(0, n - BAR_COUNT):n], prefix='bb15m'))
    return facts, float(candles['4h'][-1]['close'])


def bands(bars, period=20, width=2, prefix='bb'):
    """CCI and Bollinger bands on the last closed bar and the one before ('Prev'), with TradingView's defaults:
    CCI over 20 typical prices, bands at 2 population standard deviations around the 20-bar mean close."""
    out = {}
    for suffix, end in (('', len(bars)), ('Prev', len(bars) - 1)):
        window = bars[end - period:end]
        if len(window) < period:
            return {}
        closes = [float(b['close']) for b in window]
        typical = [(float(b['high']) + float(b['low']) + float(b['close'])) / 3 for b in window]
        mid, mean_tp = sum(closes) / period, sum(typical) / period
        sd = (sum((c - mid) ** 2 for c in closes) / period) ** 0.5
        deviation = sum(abs(t - mean_tp) for t in typical) / period
        out.update({f'{prefix}.cci{suffix}': (typical[-1] - mean_tp) / (0.015 * deviation) if deviation else 0.0,
                    f'{prefix}.upper{suffix}': mid + width * sd, f'{prefix}.mid{suffix}': mid, f'{prefix}.lower{suffix}': mid - width * sd,
                    f'{prefix}.close{suffix}': closes[-1], f'{prefix}.high{suffix}': float(window[-1]['high']), f'{prefix}.low{suffix}': float(window[-1]['low'])})
    return out


def momentum(f):
    t = trend(f)
    return t if t and side(f.get('4h.closeVsSma20Pct'), f.get('4h.change1Pct')) == t else 0


def pullback(f):
    t, rsi = trend(f), f.get('4h.rsi14', 50)
    return t if (t == 1 and rsi < 45) or (t == -1 and rsi > 55) else 0


def cci_cross(f):
    """1 when CCI crosses up through -100 on this 4h bar, -1 when it crosses down through +100."""
    if 'bb.cci' not in f:
        return 0
    if f['bb.cciPrev'] < -100 <= f['bb.cci']:
        return 1
    if f['bb.cciPrev'] > 100 >= f['bb.cci']:
        return -1
    return 0


def four_bc_mtf(f):
    """4BC step 4: the 4h CCI cross, with the 1h and 15m CCI already back past the same line and still moving that way."""
    c = cci_cross(f)
    line = -100 * c
    return c if c and all(f'{p}.cci' in f and (f[f'{p}.cci'] - line) * c > 0 and (f[f'{p}.cci'] - f[f'{p}.cciPrev']) * c > 0
                          for p in ('bb1h', 'bb15m')) else 0


# Each rule reads the facts before a 4h close and returns 1 (long), -1 (short) or 0 (no trade).
RULES = {
    'trend': ('1일 추세 방향', trend),
    'trend-200': ('1일 추세 + SMA200 (이전 스윙 규칙)', lambda f: side(f.get('1d.change20Pct'), f.get('1d.closeVsSma50Pct'), f.get('1d.closeVsSma200Pct'))),
    'momentum': ('1일 추세 + 4시간 모멘텀', momentum),
    'momentum-oi': ('1일 추세 + 4시간 모멘텀 + 미결제약정 증가', lambda f: momentum(f) if f.get('4h.oiChange1Pct', 0) > 0 else 0),
    'momentum-funding': ('1일 추세 + 4시간 모멘텀, 같은 쪽 펀딩 과열 제외', lambda f: momentum(f) if momentum(f) * f['market.fundingRatePct'] < 0.02 else 0),
    'breakout': ('1일 추세 + 4시간 20봉 돌파', breakout),
    'breakout-volume': ('1일 추세 + 4시간 20봉 돌파 + 거래량 증가 (현재 스윙 규칙)', lambda f: signals.direction(f, 'swing')),
    'pullback': ('1일 추세 + 4시간 RSI 눌림', pullback),
    'cci-cross': ('CCI ±100 되돌림 교차만', cci_cross),
    '4bc': ('4BC: 4시간 CCI ±100 되돌림 교차, 볼린저 익절·손절', cci_cross),
    '4bc-mtf': ('4BC + 1시간·15분 CCI 같은 방향 확인, 볼린저 익절·손절', four_bc_mtf),
}
BAND_EXITS = {'4bc', '4bc-mtf'}  # scored by band_trade instead of ATR targets


def trade(direction, entry, atr, path, tp, sl, hold, fee):
    """(net return, 1h bars held) of one trade: exit at the target or the stop, whichever 1h bar reaches first, else at the
    close after `hold` bars. A bar that touches both counts as the stop, since 1h bars cannot tell which came first."""
    target, stop = entry + direction * tp * atr, entry - direction * sl * atr
    exit_, held = float(path[hold - 1]['close']), hold
    for i, bar in enumerate(path[:hold]):
        high, low = float(bar['high']), float(bar['low'])
        if (low <= stop) if direction > 0 else (high >= stop):
            exit_, held = stop, i + 1
            break
        if (high >= target) if direction > 0 else (low <= target):
            exit_, held = target, i + 1
            break
    return (exit_ / entry - 1) * direction - fee, held


def trail_trade(direction, entry, atr, path, k, hold, fee):
    """(net return, 1h bars held) with an ATR trailing stop and no target: the stop starts k ATR from the entry, follows the
    best price since entry and never loosens; else exit at the close after `hold` bars. Each bar is checked against the
    stop before its own high or low moves the stop."""
    best, exit_, held = entry, float(path[hold - 1]['close']), hold
    for i, bar in enumerate(path[:hold]):
        high, low = float(bar['high']), float(bar['low'])
        stop = best - direction * k * atr
        if (low <= stop) if direction > 0 else (high >= stop):
            exit_, held = stop, i + 1
            break
        best = max(best, high) if direction > 0 else min(best, low)
    return (exit_ / entry - 1) * direction - fee, held


def one_at_a_time(trades):
    """The trades a single position takes: a signal counts only once the previous trade has exited.
    `trades` start with (entry time, net, 1h bars held) and are in time order."""
    free = 0
    for t in trades:
        if t[0] >= free:
            free = t[0] + t[2] * H
            yield t


def solo(trades):
    """One position at a time, with the nets compounded."""
    equity, peak, drawdown, taken = 1.0, 1.0, 0.0, 0
    for _, net, _ in one_at_a_time(trades):
        equity *= 1 + net
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
        taken += 1
    return {'trades': taken, 'returnPct': (equity - 1) * 100, 'maxDrawdownPct': drawdown * 100}


def band_trade(direction, entry, f, path, hold, fee):
    """(net return, 1h bars held) for the 4BC exit from the video: half at the middle band, the rest at the far band, stop at the entry-side band; what is left
    after `hold` bars exits at that close. None when the entry is not between the stop and the middle band.
    A bar that touches the stop and a target counts as the stop."""
    # shortcut: levels are frozen at the entry bar while real bands move every bar; track them per 1h bar if 4BC looks promising.
    stop, far = (f['bb.lower'], f['bb.upper']) if direction > 0 else (f['bb.upper'], f['bb.lower'])
    targets = [f['bb.mid'], far]
    if not (stop - entry) * direction < 0 < (targets[0] - entry) * direction:
        return None
    exits, rest, held = [], float(path[hold - 1]['close']), hold
    for i, bar in enumerate(path[:hold]):
        high, low = float(bar['high']), float(bar['low'])
        if (low <= stop) if direction > 0 else (high >= stop):
            rest, held = stop, i + 1
            break
        while targets and ((high >= targets[0]) if direction > 0 else (low <= targets[0])):
            exits.append(targets.pop(0))
        if not targets:
            held = i + 1
            break
    exits += [rest] * len(targets)
    return sum((p / entry - 1) * direction for p in exits) / 2 - fee, held


def research(symbol, days, tps, sls, holds, fee_pct, collector=None, now=None, folder=backtest.ARCHIVE, log=print, trails=(), chart=None):
    """Score every rule and exit setting on the 4h closes of the last `days`; the first two thirds are 'train', the rest 'test'.
    Every signal is scored, so trades overlap; 'solo' replays the same trades one position at a time.
    `chart` is one (rule, tp, sl, hold) group whose trades are kept with their levels, for chart_page."""
    collector = collector or market.Collector()
    now = now or int(time.time() * 1000)
    longest = max(holds) * H
    last = (now - longest) // STEP * STEP
    moments = list(range(last - (days * DAY) // STEP * STEP + STEP, last + 1, STEP))
    log(f'{symbol} · 4시간봉 {len(moments)}개 시점 · 캔들과 아카이브를 받는 중')
    bars = {i: klines(collector, symbol, i, moments[0] - BAR_COUNT * INTERVALS[i], moments[-1] + longest + INTERVALS[i], now) for i in FRAMES + ('15m',)}
    closes = {i: [int(b['closeTime']) for b in bars[i]] for i in bars}
    opens_1h = [int(b['openTime']) for b in bars['1h']]
    funding = funding_history(collector, symbol, moments[0] - 10 * DAY, moments[-1])
    split = moments[len(moments) * 2 // 3]
    groups, skipped, charted = {}, 0, []
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
            band = rule in BAND_EXITS
            settings = [('밴드', '밴드', h) for h in holds] if band else [*itertools.product(tps, sls, holds), *itertools.product(['추적'], trails, holds)]
            for tp, sl, hold in settings:
                g = groups.setdefault((rule, tp, sl, hold), {'rule': rule, 'tp': tp, 'sl': sl, 'holdHours': hold,
                                                             'train': [], 'test': [], 'longs': 0, 'shorts': 0})
                if not direction or len(path) < hold or int(path[hold - 1]['openTime']) != at + (hold - 1) * H:
                    continue  # no trade, or a gap in the 1h bars after entry
                fee = fee_pct / 100
                if band:
                    result = band_trade(direction, entry, facts, path, hold, fee)
                elif tp == '추적':
                    result = trail_trade(direction, entry, facts['4h.atr14'], path, sl, hold, fee)
                else:
                    result = trade(direction, entry, facts['4h.atr14'], path, tp, sl, hold, fee)
                if result is None:
                    continue  # entry already past the stop band or the middle band
                g[part].append((at, *result))
                if (rule, tp, sl, hold) == chart:
                    charted.append((at, *result, direction, entry, levels(rule, tp, sl, direction, entry, facts)))
                g['longs' if direction > 0 else 'shorts'] += 1
    results = []
    for g in groups.values():
        for part in ('train', 'test'):
            trades = g.pop(part)
            nets = [t[1] for t in trades]
            g[part] = {'trades': len(nets), 'hitRate': sum(n > 0 for n in nets) / len(nets) if nets else None,
                       'meanNetPct': sum(nets) / len(nets) * 100 if nets else None, 'solo': solo(trades)}
        results.append(g)
    data = {'moments': len(moments), 'skipped': skipped, 'split': split, 'results': results}
    if chart:
        data['chart'] = {'bars': [b for b in bars['1h'] if moments[0] - 10 * DAY <= int(b['openTime']) <= moments[-1] + longest],
                         'trades': list(one_at_a_time(charted))}
    return data


def levels(rule, tp, sl, direction, entry, f):
    """The price lines a trade starts with, as {label: price}."""
    atr = f['4h.atr14']
    if rule in BAND_EXITS:
        stop, far = (f['bb.lower'], f['bb.upper']) if direction > 0 else (f['bb.upper'], f['bb.lower'])
        return {'익절 1 (중앙선)': f['bb.mid'], '익절 2 (반대 밴드)': far, '손절 (밴드)': stop}
    if tp == '추적':
        return {'최초 손절 (추적)': entry - direction * sl * atr}
    return {'익절': entry + direction * tp * atr, '손절': entry - direction * sl * atr}


def chart_page(symbol, title, bars, trades, fee):
    """A standalone HTML page: 1h candles in KST with each one-at-a-time trade's entry, exit and starting price lines."""
    kst = lambda ms: int(ms) // 1000 + 9 * 3600  # the chart library labels times in UTC; shift so they read as KST
    candles = [{'time': kst(b['openTime']), 'open': float(b['open']), 'high': float(b['high']), 'low': float(b['low']), 'close': float(b['close'])} for b in bars]
    rows = []
    for at, net, held, direction, entry, lines in trades:
        exit_at = at + (held - 1) * H  # open of the 1h bar the trade left in
        rows.append({'entry': kst(at), 'exit': kst(exit_at), 'side': direction, 'price': entry, 'lines': lines,
                     'exitPrice': entry * (1 + direction * (net + fee)), 'netPct': net * 100,
                     'date': market.iso(at + 9 * H)[:16].replace('T', ' ')})
    payload = json.dumps({'title': f'{symbol} · {title}', 'candles': candles, 'trades': rows}, ensure_ascii=False)
    return CHART_HTML.replace('__DATA__', payload.replace('</', '<\\/'))


CHART_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>규칙 진입 차트</title>
<style>
:root { --bg: #ffffff; --fg: #1f2328; --muted: #656d76; --line: #d0d7de; --up: #1a7f37; --down: #cf222e; --row: #f6f8fa; }
@media (prefers-color-scheme: dark) { :root { --bg: #0d1117; --fg: #e6edf3; --muted: #8d96a0; --line: #30363d; --up: #3fb950; --down: #f85149; --row: #161b22; } }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 14px/1.5 system-ui, sans-serif; }
h1 { font-size: 18px; margin: 0 0 4px; } p { margin: 0 0 12px; color: var(--muted); }
#chart { height: 520px; border: 1px solid var(--line); border-radius: 6px; }
.wrap { overflow-x: auto; margin-top: 12px; } table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align: left; }
tbody tr { cursor: pointer; } tbody tr:hover { background: var(--row); }
.up { color: var(--up); } .down { color: var(--down); }
</style></head><body>
<h1 id="title"></h1>
<p>한 번에 한 포지션만 든다고 볼 때의 거래입니다. ▲ 롱 진입, ▼ 숏 진입, ● 청산. 초록·파랑 실선은 익절, 빨강 점선은 손절 위치(진입 때 정한 값)이고, 시간은 한국 시간입니다. 표의 행을 누르면 그 거래로 이동합니다.</p>
<div id="chart"></div>
<div class="wrap"><table><thead><tr><th>진입 시각</th><th>방향</th><th>진입가</th><th>익절·손절</th><th>청산가</th><th>순수익</th></tr></thead><tbody id="rows"></tbody></table></div>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<script>
const data = __DATA__;
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
document.getElementById('title').textContent = data.title;
const box = document.getElementById('chart');
const chart = LightweightCharts.createChart(box, { autoSize: true, layout: { background: { color: css('--bg') }, textColor: css('--fg') },
  grid: { vertLines: { color: css('--line') }, horzLines: { color: css('--line') } }, timeScale: { timeVisible: true } });
const candles = chart.addCandlestickSeries({ upColor: css('--up'), downColor: css('--down'), wickUpColor: css('--up'), wickDownColor: css('--down'), borderVisible: false });
candles.setData(data.candles);
const fmt = v => v.toLocaleString('ko-KR', { maximumFractionDigits: v < 10 ? 4 : 2 });
const markers = [];
const lineOptions = label => ({ color: COLORS[label] || css('--muted'), lineWidth: 2, lineStyle: label.includes('손절') ? 2 : 0,
  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
const COLORS = { '익절': '#2da44e', '익절 1 (중앙선)': '#2da44e', '익절 2 (반대 밴드)': '#1f6feb', '손절': '#cf222e', '손절 (밴드)': '#cf222e', '최초 손절 (추적)': '#cf222e' };
for (const t of data.trades) {
  const long = t.side > 0;
  markers.push({ time: t.entry, position: long ? 'belowBar' : 'aboveBar', color: long ? css('--up') : css('--down'), shape: long ? 'arrowUp' : 'arrowDown', text: (long ? '롱 ' : '숏 ') + fmt(t.price) });
  markers.push({ time: t.exit, position: 'inBar', color: t.netPct > 0 ? css('--up') : css('--down'), shape: 'circle', text: (t.netPct > 0 ? '+' : '') + t.netPct.toFixed(2) + '%' });
  // One short series per line: a shared series would join one trade's line to the next.
  for (const [label, price] of Object.entries(t.lines))
    chart.addLineSeries(lineOptions(label)).setData([{ time: t.entry, value: price }, { time: Math.max(t.exit, t.entry + 3600), value: price }]);
}
markers.sort((a, b) => a.time - b.time);
candles.setMarkers(markers);
const tbody = document.getElementById('rows');
for (const t of data.trades) {
  const tr = document.createElement('tr');
  const cells = [t.date, t.side > 0 ? '롱' : '숏', fmt(t.price), Object.entries(t.lines).map(([k, v]) => k + ' ' + fmt(v)).join(' · '), fmt(t.exitPrice), (t.netPct > 0 ? '+' : '') + t.netPct.toFixed(2) + '%'];
  cells.forEach((text, i) => { const td = document.createElement('td'); td.textContent = text; if (i === 5) td.className = t.netPct > 0 ? 'up' : 'down'; tr.append(td); });
  tr.addEventListener('click', () => { chart.timeScale().setVisibleRange({ from: t.entry - 3 * 86400, to: t.exit + 3 * 86400 }); box.scrollIntoView({ behavior: 'smooth' }); });
  tbody.append(tr);
}
const last = data.trades.at(-1);
if (last) chart.timeScale().setVisibleRange({ from: last.entry - 30 * 86400, to: last.exit + 5 * 86400 });
</script></body></html>
"""


def report(data, min_trades, top, log=print):
    log(f"시점 {data['moments']}개 (건너뜀 {data['skipped']}) · 학습 구간 ~ {market.iso(data['split'])[:10]} · 검증 구간 그 이후")
    log('규칙 | 목표·손절(ATR배; 추적은 추적 손절 ATR배, 4BC는 볼린저 밴드)·보유 | 학습: 진입·적중률·평균순수익 / 단일 포지션 진입·누적·최대낙폭 | 검증: 같음')
    fmt = lambda p: (f"{p['trades']:4d} · {'-' if p['hitRate'] is None else format(p['hitRate'], '4.0%')} · {'-' if p['meanNetPct'] is None else format(p['meanNetPct'], '+.2f') + '%'}"
                     f" / {p['solo']['trades']:3d} · {p['solo']['returnPct']:+.1f}% · -{p['solo']['maxDrawdownPct']:.1f}%")
    # Ranked by the train-period solo return: hit rate alone buries trend exits, which win rarely but big.
    ranked = sorted(data['results'], key=lambda g: (g['train']['trades'] >= min_trades, g['train']['solo']['returnPct']), reverse=True)
    for g in ranked[:top]:
        flag = '' if g['train']['trades'] >= min_trades else '  (진입 부족)'
        log(f"{RULES[g['rule']][0]} | {g['tp']}·{g['sl']}·{g['holdHours']}h | {fmt(g['train'])} | {fmt(g['test'])}{flag}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='4시간봉 규칙 후보를 목표가·손절 중 먼저 닿는 쪽으로 채점합니다. 모델은 실행하지 않습니다.')
    parser.add_argument('--symbol', default='BTCUSDT', choices=market.SYMBOLS)
    parser.add_argument('--days', type=int, default=180, help='오늘부터 거슬러 올라갈 일수')
    parser.add_argument('--tp', type=float, nargs='+', default=[1.0, 1.5, 2.0], help='목표가: 4시간 ATR의 몇 배')
    parser.add_argument('--sl', type=float, nargs='+', default=[1.0, 1.5], help='손절: 4시간 ATR의 몇 배')
    parser.add_argument('--trail', type=float, nargs='*', default=[2.0, 3.0], help='추적 손절: 4시간 ATR의 몇 배 (빈 값이면 생략)')
    parser.add_argument('--hold-hours', type=int, nargs='+', default=[12, 24], help='최대 보유 시간')
    parser.add_argument('--fee', type=float, default=0.1, help='왕복 수수료(%%), 기본 테이커 0.05%% x 2')
    parser.add_argument('--min-trades', type=int, default=30, help='학습 구간 최소 진입 수')
    parser.add_argument('--top', type=int, default=30, help='표시할 줄 수')
    parser.add_argument('--chart', nargs=4, metavar=('RULE', 'TP', 'SL', 'HOLD'),
                        help='이 규칙·청산의 거래를 reports/에 차트로 저장. 예: breakout-volume 2 1 168, trend 추적 3 168, 4bc 밴드 밴드 720')
    args = parser.parse_args()
    if args.days < 7 or min(args.tp + args.sl + args.trail) <= 0 or min(args.hold_hours) <= 0:
        parser.error('--days는 7 이상, 목표·손절·추적 손절·보유 시간은 0보다 커야 합니다.')
    chart = None
    if args.chart:
        rule, tp, sl, hold = args.chart
        number = lambda v: v if v in ('추적', '밴드') else float(v)
        chart = (rule, number(tp), float(sl) if tp == '추적' else number(sl), int(hold))
        if rule not in RULES or int(hold) not in args.hold_hours:
            parser.error(f'--chart: 규칙은 {", ".join(RULES)} 중 하나, 보유 시간은 --hold-hours 안의 값이어야 합니다.')
    data = research(args.symbol, args.days, args.tp, args.sl, args.hold_hours, args.fee, trails=args.trail, chart=chart)
    report(data, args.min_trades, args.top)
    if chart:
        if not data['chart']['trades']:
            raise SystemExit('--chart: 그 규칙·청산 조합의 거래가 없습니다. TP·SL이 --tp·--sl(또는 --trail) 안의 값인지 확인하세요.')
        out = Path(__file__).resolve().parent / 'reports' / f'chart-{args.symbol}-{rule}.html'
        out.parent.mkdir(exist_ok=True)
        title = f"{RULES[rule][0]} · {args.chart[1]}·{args.chart[2]}·{hold}h"
        out.write_text(chart_page(args.symbol, title, data['chart']['bars'], data['chart']['trades'], args.fee / 100), encoding='utf-8')
        print(f'차트: {out}')
