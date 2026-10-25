"""Read-only Binance USD-M market collection. No credentials or trading endpoints."""
from contextlib import closing
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import indicators

BASE = 'https://fapi.binance.com'
SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')
INTERVALS = {'5m': 300000, '15m': 900000, '1h': 3600000, '4h': 14400000, '1d': 86400000}
BAR_COUNT = 200
DB = Path(__file__).resolve().parent / 'data' / 'market.sqlite3'


class MarketError(Exception):
    pass


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds')


def timestamp(value):
    if type(value) is not int or not 0 < value < 4102444800000:
        raise MarketError('거래소 타임스탬프 형식 오류')
    return value


def number(value, positive=False, signed=False):
    if not isinstance(value, str) or len(value) > 50:
        raise MarketError('거래소 숫자 형식 오류')
    try:
        result = Decimal(value)
        if not result.is_finite() or (not signed and result < 0) or (positive and result <= 0):
            raise InvalidOperation
        return result
    except InvalidOperation as exc:
        raise MarketError('거래소 숫자 범위 오류') from exc


def closed_bars(rows, interval, as_of):
    if not isinstance(rows, list) or not rows or len(rows) > BAR_COUNT + 1:
        raise MarketError('캔들 응답 개수 오류')
    step = INTERVALS[interval]
    bars, previous = [], None
    for row in rows:
        if not isinstance(row, list) or len(row) != 12:
            raise MarketError('캔들 응답 형식 오류')
        opened, closed = timestamp(row[0]), timestamp(row[6])
        if opened % step or closed != opened + step - 1:
            raise MarketError('캔들 시작·마감 시각 오류')
        if previous is not None and opened != previous + step:
            raise MarketError('캔들 누락·중복·순서 오류')
        previous = opened
        o, h, low, c = [number(value, positive=True) for value in row[1:5]]
        if not low <= min(o, c) <= max(o, c) <= h:
            raise MarketError('캔들 OHLC 범위 오류')
        for index in (5, 7, 9, 10):
            number(row[index])
        if type(row[8]) is not int or row[8] < 0:
            raise MarketError('캔들 거래 건수 오류')
        if opened > as_of:
            raise MarketError('미래 캔들 응답 오류')
        if closed >= as_of:
            continue
        bars.append(dict(zip(
            ('openTime', 'open', 'high', 'low', 'close', 'volume', 'closeTime',
             'quoteVolume', 'trades', 'takerBuyVolume', 'takerBuyQuoteVolume'), row[:11])))
    if not bars:
        raise MarketError('마감된 캔들이 없습니다.')
    if as_of - bars[-1]['closeTime'] > step + 10000:
        raise MarketError('최근 마감 캔들이 누락되었거나 오래되었습니다.')
    return bars[-BAR_COUNT:]


def save_candles(db, symbol, candles):
    db.execute('CREATE TABLE IF NOT EXISTS candles (symbol TEXT, interval TEXT, open_time INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,open_time))')
    for interval, bars in candles.items():
        db.executemany('INSERT OR REPLACE INTO candles VALUES (?,?,?,?)',
                       [(symbol, interval, bar['openTime'], json.dumps(bar)) for bar in bars])


def save_snapshot(snapshot, path=DB):
    """Keep the snapshot's closed bars, which scoring reads later. The rest of the snapshot was never read back, so it is not kept."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        save_candles(db, snapshot['symbol'], snapshot['candles'])


# Positioning history the explainer reads: 4h for the call's own frame, 1h for the moves inside it.
HISTORY_PERIODS = {'1h': 3600000, '4h': 14400000}
HISTORY_COUNT = 25
FUNDING_COUNT = 21
HISTORY_ENDPOINTS = {
    'openInterest': ('/futures/data/openInterestHist', ('sumOpenInterest', 'sumOpenInterestValue')),
    'globalLongShort': ('/futures/data/globalLongShortAccountRatio', ('longShortRatio', 'longAccount', 'shortAccount')),
    'topLongShort': ('/futures/data/topLongShortPositionRatio', ('longShortRatio', 'longAccount', 'shortAccount')),
    'taker': ('/futures/data/takerlongshortRatio', ('buySellRatio', 'buyVol', 'sellVol')),
}


def history_rows(rows, symbol, fields, time_key='timestamp', signed=()):
    if not isinstance(rows, list) or not rows or len(rows) > 1000:
        raise MarketError('수급 이력 응답 개수 오류')
    result, previous = [], None
    for row in rows:
        if not isinstance(row, dict) or row.get('symbol', symbol) != symbol:
            raise MarketError('수급 이력 응답 종목 또는 형식 오류')
        stamp = timestamp(row[time_key])
        if previous is not None and stamp <= previous:
            raise MarketError('수급 이력 시각 순서 오류')
        previous = stamp
        for field in fields:
            number(row[field], signed=field in signed)
        result.append({'time': stamp, **{field: row[field] for field in fields}})
    return result


def save_derivatives(symbol, history, path=DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS derivatives (symbol TEXT, metric TEXT, period TEXT, time INTEGER, payload TEXT, PRIMARY KEY(symbol,metric,period,time))')
        rows = [(symbol, 'funding', '8h', r['time'], json.dumps(r)) for r in history['funding']]
        for period, metrics in history['periods'].items():
            rows += [(symbol, metric, period, r['time'], json.dumps(r)) for metric, data in metrics.items() for r in data]
        db.executemany('INSERT OR REPLACE INTO derivatives VALUES (?,?,?,?,?)', rows)


class Collector:
    def __init__(self, db_path=DB):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.cache = {}
        self.retry_at = 0

    def request(self, path, **params):
        if time.monotonic() < self.retry_at:
            raise MarketError('Binance 요청 제한 대기 중입니다. 잠시 후 다시 시도하세요.')
        request = Request(BASE + path + ('?' + urlencode(params) if params else ''),
                          headers={'User-Agent': 'FuturesDesk/1.0', 'Accept': 'application/json'})
        try:
            with urlopen(request, timeout=8) as response:
                payload = response.read(2_000_001)
                if len(payload) > 2_000_000:
                    raise MarketError('거래소 응답 크기 초과')
                return json.loads(payload)
        except HTTPError as exc:
            if exc.code in (418, 429):
                try:
                    delay = max(60, int(exc.headers.get('Retry-After', '120')))
                except ValueError:
                    delay = 120
                self.retry_at = time.monotonic() + delay
                raise MarketError(f'Binance 요청 제한: {delay}초 후 재시도 가능합니다.') from exc
            raise MarketError(f'Binance HTTP {exc.code}: 데이터 수집에 실패했습니다.') from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise MarketError('Binance 연결 실패 또는 JSON 응답 오류. 네트워크를 확인하세요.') from exc

    def collect(self, symbol):
        if symbol not in SYMBOLS:
            raise ValueError('지원 종목은 BTCUSDT·ETHUSDT·SOLUSDT입니다.')
        with self.lock:
            cached = self.cache.get(symbol)
            if cached and time.monotonic() - cached[0] < 30:
                return {**cached[1], 'cacheAgeMs': round((time.monotonic() - cached[0]) * 1000)}
            started = time.monotonic()
            local_start = time.time() * 1000
            try:
                server_time = timestamp(self.request('/fapi/v1/time')['serverTime'])
                ticker = self.request('/fapi/v2/ticker/price', symbol=symbol)
                mark = self.request('/fapi/v1/premiumIndex', symbol=symbol)
                interest = self.request('/fapi/v1/openInterest', symbol=symbol)
                warnings = []
                for payload in (ticker, mark, interest):
                    if payload['symbol'] != symbol:
                        raise MarketError('응답 종목이 요청과 다릅니다.')
                    stamp = timestamp(payload['time'])
                    age = server_time - stamp
                    if age > 60000:
                        warnings.append('시세 또는 수급의 기준 시각이 60초 이상 오래되었습니다.')
                    if age < -10000:
                        raise MarketError('시세 또는 수급 시각이 서버 시각보다 지나치게 미래입니다.')
                number(ticker['price'], positive=True)
                number(mark['markPrice'], positive=True)
                number(mark['indexPrice'], positive=True)
                number(mark['lastFundingRate'], signed=True)
                number(interest['openInterest'])
                timestamp(mark['nextFundingTime'])
                if mark['nextFundingTime'] <= server_time:
                    warnings.append('다음 펀딩 시각이 지났습니다. 정산 직후에는 다시 수집하세요.')
                candles = {}
                for interval in INTERVALS:
                    rows = self.request('/fapi/v1/klines', symbol=symbol, interval=interval,
                                        limit=BAR_COUNT + 1, endTime=server_time - 1)
                    candles[interval] = closed_bars(rows, interval, server_time)
                    if len(candles[interval]) < BAR_COUNT:
                        warnings.append(f'{interval} 마감 봉이 {BAR_COUNT}개보다 적습니다.')
                if abs(server_time - local_start) > 5000:
                    warnings.append('PC와 거래소 시각 차이가 5초 이상입니다. 마감 판단은 거래소 시각을 사용합니다.')
                elapsed = time.monotonic() - started
                if elapsed > 30:
                    warnings.append('수집에 30초 이상 걸렸습니다. 항목별 기준 시각을 확인하세요.')
                result = {
                    'schemaVersion': 1, 'kind': 'market-data', 'source': BASE,
                    'symbol': symbol, 'market': 'USDⓈ-M perpetual', 'baseAsset': symbol[:-4],
                    'serverTime': server_time, 'collectedAt': iso(time.time() * 1000),
                    'cacheAgeMs': 0, 'collectionDurationMs': round(elapsed * 1000),
                    'clockOffsetMs': round(server_time - local_start),
                    'ticker': ticker, 'mark': mark, 'openInterest': interest,
                    'candles': candles, 'warnings': list(dict.fromkeys(warnings)),
                    'candleSummary': {i: {'count': len(b), 'lastCloseTime': b[-1]['closeTime'],
                                          'lastClose': b[-1]['close']} for i, b in candles.items()},
                    'endpoints': ['/fapi/v1/time', '/fapi/v2/ticker/price', '/fapi/v1/premiumIndex',
                                  '/fapi/v1/openInterest', '/fapi/v1/klines'],
                }
                result['indicators'] = indicators.compute(result)
                save_snapshot(result, self.db_path)
            except (KeyError, TypeError, IndexError) as exc:
                raise MarketError('거래소 응답 필드가 누락되거나 형식이 변경되었습니다.') from exc
            except (sqlite3.Error, OSError) as exc:
                raise MarketError('로컬 데이터 저장에 실패했습니다. 디스크 공간과 권한을 확인하세요.') from exc
            self.cache[symbol] = (time.monotonic(), result)
            return result

    def derivatives(self, symbol):
        """Funding, OI, long/short and taker history; collected only when an analysis runs."""
        if symbol not in SYMBOLS:
            raise ValueError('지원 종목은 BTCUSDT·ETHUSDT·SOLUSDT입니다.')
        with self.lock:
            try:
                server_time = timestamp(self.request('/fapi/v1/time')['serverTime'])
                funding = history_rows(self.request('/fapi/v1/fundingRate', symbol=symbol, limit=FUNDING_COUNT),
                                       symbol, ('fundingRate',), 'fundingTime', signed=('fundingRate',))
                periods, warnings = {}, []
                for period, step in HISTORY_PERIODS.items():
                    periods[period] = {}
                    for metric, (path, fields) in HISTORY_ENDPOINTS.items():
                        data = history_rows(self.request(path, symbol=symbol, period=period, limit=HISTORY_COUNT), symbol, fields)
                        if data[-1]['time'] > server_time + 10000:
                            raise MarketError('수급 이력 시각이 서버 시각보다 미래입니다.')
                        if server_time - data[-1]['time'] > 2 * step + 60000:
                            warnings.append(f'{period} {metric} 이력의 최근 값이 오래되었습니다.')
                        if len(data) < HISTORY_COUNT:
                            warnings.append(f'{period} {metric} 이력이 {HISTORY_COUNT}개보다 적습니다.')
                        periods[period][metric] = data
                if server_time - funding[-1]['time'] > 9 * 3600000:
                    warnings.append('펀딩 이력의 최근 정산이 9시간 이상 지났습니다.')
                history = {'serverTime': server_time, 'funding': funding, 'periods': periods, 'warnings': warnings}
                save_derivatives(symbol, history, self.db_path)
                return history
            except (KeyError, TypeError, IndexError) as exc:
                raise MarketError('수급 이력 응답 필드가 누락되거나 형식이 변경되었습니다.') from exc
            except (sqlite3.Error, OSError) as exc:
                raise MarketError('수급 이력 저장에 실패했습니다. 디스크 공간과 권한을 확인하세요.') from exc
