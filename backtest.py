"""Replays past moments through the code's rules (signals.py). Run: python backtest.py --symbol BTCUSDT --days 90
Each snapshot is rebuilt only from Binance data published before that moment; calls are saved and scored as mode 'backtest'.
No model runs: the calls never depend on one, so a backtest scores exactly what the live analysis would have called."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
import io
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import journal
import local_llm
import market
import signals
from market import BAR_COUNT, INTERVALS, MarketError, closed_bars

STEP = INTERVALS['4h']  # moments sit on 4h boundaries, so every interval's last bar closes exactly there
DAY = 86400000
NEWEST = max(hold for _, hold in journal.HOLDS.values())  # the longest hold must be over to score the call
# Binance's REST OI and long/short history only reaches about 30 days back; its bulk archive keeps 5-minute values for years.
ARCHIVE = market.DB.parent / 'archive'
ARCHIVE_URL = 'https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day}.zip'
METRIC_COLUMNS = ('create_time', 'symbol', 'sum_open_interest', 'sum_open_interest_value', 'count_toptrader_long_short_ratio',
                  'sum_toptrader_long_short_ratio', 'count_long_short_ratio', 'sum_taker_long_short_vol_ratio')


def bars_before(collector, at, interval, limit=BAR_COUNT + 1, path='/fapi/v1/klines', **params):
    rows = collector.request(path, interval=interval, limit=limit, endTime=at - 1, **params)
    return closed_bars(rows, interval, at)


@lru_cache(maxsize=16)
def metrics_day(symbol, day, folder=ARCHIVE):
    """One UTC day of 5-minute OI and long/short values by time, downloaded once and kept as CSV."""
    path = Path(folder) / symbol / f'{day}.csv'
    if not path.exists():
        request = Request(ARCHIVE_URL.format(symbol=symbol, day=day), headers={'User-Agent': 'FuturesDesk/1.0'})
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read(5_000_001)
            if len(payload) > 5_000_000:
                raise MarketError('아카이브 파일 크기 초과')
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                text = archive.read(archive.namelist()[0]).decode('utf-8')
        except HTTPError as exc:
            raise MarketError(f'{day} 아카이브 파일이 없습니다(HTTP {exc.code}). 최근 날짜는 하루쯤 뒤에 올라옵니다.') from exc
        except (URLError, OSError, zipfile.BadZipFile, IndexError, UnicodeDecodeError) as exc:
            raise MarketError(f'{day} 아카이브를 받지 못했습니다.') from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(text, encoding='utf-8')
        temp.replace(path)  # a half-written file is never taken for a finished one
    lines = path.read_text(encoding='utf-8').splitlines()
    if not lines or tuple(lines[0].split(',')) != METRIC_COLUMNS:
        raise MarketError(f'{day} 아카이브 열 구성이 바뀌었습니다.')
    rows = {}
    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) != len(METRIC_COLUMNS) or parts[1] != symbol:
            raise MarketError(f'{day} 아카이브 행 형식 오류')
        try:
            at = int(datetime.fromisoformat(parts[0]).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError as exc:
            raise MarketError(f'{day} 아카이브 시각 형식 오류') from exc
        try:
            for value in parts[2:]:
                market.number(value, positive=True)
        except MarketError:
            continue  # the archive has occasional zero rows (e.g. OI value 0E-16 on 2026-10-15); that time counts as missing
        rows[at] = dict(zip(METRIC_COLUMNS, parts))
    return rows


def metrics_at(symbol, at, folder=ARCHIVE):
    # The archive has short outages (OI 0 from 13:40 to 14:45 on 2026-10-15), so the last value up to 30 minutes earlier stands in.
    for t in range(at, at - 7 * INTERVALS['5m'], -INTERVALS['5m']):
        row = metrics_day(symbol, market.iso(t)[:10], folder).get(t)
        if row is not None:
            return row
    raise MarketError(f'아카이브에 {market.iso(at)}부터 30분 전까지 값이 없습니다.')


def ratio_row(at, ratio):
    r = Decimal(ratio)
    return {'timestamp': at, 'longShortRatio': ratio, 'longAccount': f'{r / (1 + r):.4f}', 'shortAccount': f'{1 / (1 + r):.4f}'}


def derivatives_at(symbol, at, candles, funding, folder=ARCHIVE):
    """The same history the live analysis fetches, as it stood just before `at`, given the funding settled before it.
    OI and long/short come from the archive (within about 0.3% of the REST values at 1h); taker ratios from klines, as REST computes them."""
    periods = {}
    for period, step in market.HISTORY_PERIODS.items():
        times = [at - k * step for k in range(market.HISTORY_COUNT, 0, -1)]
        metrics = [metrics_at(symbol, t, folder) for t in times]
        bars = {b['openTime']: b for b in candles[period]}
        if any(t not in bars for t in times):
            raise MarketError(f'{period} 캔들이 모자라 테이커 비율을 계산할 수 없습니다.')
        taker = []
        for t in times:
            buy = Decimal(bars[t]['takerBuyVolume'])
            sell = Decimal(bars[t]['volume']) - buy
            if sell <= 0:
                raise MarketError(f'{market.iso(t)} 매도 체결량이 없어 테이커 비율을 계산할 수 없습니다.')
            taker.append({'timestamp': t, 'buySellRatio': f'{buy / sell:.4f}', 'buyVol': str(buy), 'sellVol': str(sell)})
        rows = {'openInterest': [{'timestamp': t, 'sumOpenInterest': m['sum_open_interest'], 'sumOpenInterestValue': m['sum_open_interest_value']}
                                 for t, m in zip(times, metrics)],
                'globalLongShort': [ratio_row(t, m['count_long_short_ratio']) for t, m in zip(times, metrics)],
                'topLongShort': [ratio_row(t, m['sum_toptrader_long_short_ratio']) for t, m in zip(times, metrics)],
                'taker': taker}
        periods[period] = {metric: market.history_rows(rows[metric], symbol, fields)
                           for metric, (_, fields) in market.HISTORY_ENDPOINTS.items()}
    return {'serverTime': at, 'funding': funding, 'periods': periods, 'warnings': []}


def snapshot_at(collector, symbol, at):
    """The candles closed before `at`. The rules read nothing else; funding and positioning only fed the explainer,
    which backtests do not run, so a gap in Binance's archive no longer skips a moment."""
    candles = {i: bars_before(collector, at, i, symbol=symbol) for i in INTERVALS}
    return {'symbol': symbol, 'serverTime': at, 'warnings': [], 'candles': candles, 'ticker': {'price': candles['5m'][-1]['close']}}


def exit_bars(collector, symbol, at):
    """Every bar of each horizon's hold, so scores() can follow a target and stop without the live collector."""
    return {interval: bars_before(collector, at + hold, interval, hold // INTERVALS[interval], symbol=symbol)
            for interval, hold in journal.HOLDS.values()}


def moments(now, every, days):
    """Newest first, so a run cut short still covers the most recent stretch."""
    newest = (now - NEWEST) // STEP * STEP
    return list(range(newest, now - days * DAY, -every))


def done(path, symbol):
    with closing(journal.connect(path)) as db:
        return {row[0] for row in db.execute("SELECT as_of FROM reports WHERE mode='backtest' AND symbol=? AND prompt_version=?",
                                             (symbol, signals.VERSION))}


def run(symbol, days=90, every_hours=4, limit=None, collector=None, now=None, log=print):
    collector = collector or market.Collector()
    now = now or int(time.time() * 1000)
    seen = done(collector.db_path, symbol)
    todo = [at for at in moments(now, every_hours * 3600000, days) if at not in seen][:limit]
    log(f'{symbol} · 규칙 {signals.VERSION} · 이미 분석 {len(seen)}건 · 이번에 {len(todo)}건')
    saved = 0
    for n, at in enumerate(todo, 1):
        try:
            snapshot = snapshot_at(collector, symbol, at)
            exits = exit_bars(collector, symbol, at)
            report = {**local_llm.analyze(snapshot), 'mode': 'backtest'}
            with closing(journal.connect(collector.db_path)) as db, db:
                market.save_candles(db, symbol, {i: snapshot['candles'][i] + exits[i] for i in exits})
            journal.save_report(report, collector.db_path)
        except MarketError as exc:
            log(f'[{n}/{len(todo)}] {market.iso(at)} 건너뜀: {exc}')
            continue
        saved += 1
        verdicts = ' · '.join(f"{h} {d['verdict']}" for h, d in report['horizons'].items())
        log(f'[{n}/{len(todo)}] {market.iso(at)} {verdicts}')
    return saved


def summary(path, log=print):
    for g in journal.scores(path):
        if g['mode'] == 'backtest' and g['role'] == 'judge':
            hit = '-' if g['hitRate'] is None else f"{g['hitRate']:.0%}"
            ret = '-' if g['meanReturnPct'] is None else f"{g['meanReturnPct']:+.2f}%"
            log(f"{g['promptVersion']} {g['horizon']}: 판정 {g['calls']} · 채점 {g['scored']} · 적중률 {hit} · 평균 {ret} · 관망 {g['waitRate']:.0%}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='저장된 판정 채점과 같은 기준으로 과거 시점에 코드 규칙을 다시 적용합니다. 모델은 실행하지 않습니다.')
    parser.add_argument('--symbol', default='BTCUSDT', choices=market.SYMBOLS)
    parser.add_argument('--days', type=int, default=90, help='오늘부터 거슬러 올라갈 일수')
    parser.add_argument('--every-hours', type=int, default=4, help='분석 시점 간격(4의 배수)')
    parser.add_argument('--limit', type=int, help='이번 실행에서 분석할 최대 시점 수')
    args = parser.parse_args()
    if args.every_hours <= 0 or args.every_hours % 4:
        parser.error('--every-hours는 4의 배수여야 합니다.')
    if args.days <= 3:
        parser.error('--days는 4 이상이어야 합니다. 최근 3일은 아직 채점할 수 없습니다.')
    run(args.symbol, args.days, args.every_hours, args.limit)
    summary(market.DB)
