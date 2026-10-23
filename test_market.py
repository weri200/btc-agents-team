from contextlib import closing
import json
import sqlite3
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import market
import server


NOW = 1792625571000


def rows(interval):
    step = market.INTERVALS[interval]
    start = NOW // step * step
    return [[start + i * step, '100', '102', '99', '101', '10',
             start + (i + 1) * step - 1, '1000', 5, '4', '400', '0']
            for i in (-2, -1, 0)]


def response(path, **params):
    symbol = params.get('symbol', 'BTCUSDT')
    if path.endswith('/time'):
        return {'serverTime': NOW}
    if path.endswith('/price'):
        return {'symbol': symbol, 'price': '101', 'time': NOW}
    if path.endswith('/premiumIndex'):
        return {'symbol': symbol, 'markPrice': '100.5', 'indexPrice': '100',
                'lastFundingRate': '-0.0001', 'nextFundingTime': NOW + 100000, 'time': NOW}
    if path.endswith('/openInterest'):
        return {'symbol': symbol, 'openInterest': '1000', 'time': NOW}
    return rows(params['interval'])


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'market.sqlite3'
        self.collector = market.Collector(self.path)

    def test_closed_only_and_ohlcv_strings(self):
        bars = market.closed_bars(rows('5m'), '5m', NOW)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1]['close'], '101')
        self.assertLess(bars[-1]['closeTime'], NOW)

    def test_boundary_requires_close_before_server_time(self):
        data = rows('5m')
        bars = market.closed_bars(data[:2], '5m', data[1][6])
        self.assertEqual(len(bars), 1)

    def test_rejects_bad_candles(self):
        for mutate in [lambda r: r.pop(1), lambda r: r.insert(1, r[0]),
                       lambda r: r.reverse(), lambda r: r[0].__setitem__(2, 'NaN'),
                       lambda r: r[0].__setitem__(3, '200'),
                       lambda r: r[0].__setitem__(5, '-1'),
                       lambda r: r[0].__setitem__(6, NOW),
                       lambda r: r[0].__setitem__(8, -1)]:
            data = rows('5m')
            mutate(data)
            with self.subTest(data=data), self.assertRaises(market.MarketError):
                market.closed_bars(data, '5m', NOW)
        with self.assertRaises(market.MarketError):
            market.closed_bars(rows('5m')[:1], '5m', NOW)

    def test_collection_cache_and_sqlite_dedup(self):
        with patch.object(self.collector, 'request', side_effect=response) as request:
            result = self.collector.collect('BTCUSDT')
            self.assertEqual(request.call_count, 9)
            self.assertEqual(self.collector.collect('BTCUSDT')['collectedAt'], result['collectedAt'])
            self.assertEqual(request.call_count, 9)
        market.save_snapshot(result, self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM candles').fetchone()[0], 10)
            self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 1)
        self.assertEqual(result['mark']['lastFundingRate'], '-0.0001')

    def test_invalid_symbol_never_calls_upstream(self):
        with patch.object(self.collector, 'request') as request:
            with self.assertRaises(ValueError):
                self.collector.collect('../../secret')
            request.assert_not_called()

    def test_partial_failure_does_not_store(self):
        def fail(path, **params):
            if params.get('interval') == '1h':
                raise market.MarketError('timeout')
            return response(path, **params)
        with patch.object(self.collector, 'request', side_effect=fail):
            with self.assertRaises(market.MarketError):
                self.collector.collect('BTCUSDT')
        self.assertFalse(self.path.exists())
        self.assertEqual(self.collector.cache, {})

    def test_mismatch_and_nonfinite_rejected(self):
        for change in ({'symbol': 'ETHUSDT'}, {'price': 'Infinity'}, {'time': None}):
            def bad(path, **params):
                data = response(path, **params)
                if path.endswith('/price'):
                    data.update(change)
                return data
            with patch.object(self.collector, 'request', side_effect=bad), self.assertRaises(market.MarketError):
                self.collector.collect('BTCUSDT')

    def test_old_metric_is_flagged(self):
        def old(path, **params):
            data = response(path, **params)
            if path.endswith('/openInterest'):
                data['time'] -= 120000
            return data
        with patch.object(self.collector, 'request', side_effect=old):
            result = self.collector.collect('BTCUSDT')
        self.assertTrue(any('60초' in w for w in result['warnings']))

    def test_rate_limit_cooldown(self):
        error = HTTPError('url', 429, 'limit', {'Retry-After': '60'}, None)
        with patch('market.urlopen', side_effect=error) as request:
            for _ in range(2):
                with self.assertRaises(market.MarketError):
                    self.collector.request('/fapi/v1/time')
            self.assertEqual(request.call_count, 1)

    def test_network_failure(self):
        with patch('market.urlopen', side_effect=URLError('offline')):
            with self.assertRaises(market.MarketError):
                self.collector.request('/fapi/v1/time')

    def test_storage_failure_is_not_success(self):
        with patch.object(self.collector, 'request', side_effect=response), patch('market.save_snapshot', side_effect=sqlite3.OperationalError('full')):
            with self.assertRaises(market.MarketError):
                self.collector.collect('BTCUSDT')
        self.assertFalse(self.collector.cache)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def get(self, path, headers=None):
        connection = HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        connection.request('GET', path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_static_files_and_private_paths(self):
        self.assertEqual(self.get('/')[0], 200)
        for path in ('/data/market.sqlite3', '/market.py', '/../README.md', '/.git/config'):
            self.assertEqual(self.get(path)[0], 404)

    def test_api_validation_and_origin(self):
        for path in ('/api/market', '/api/market?symbol=BTCUSDT&symbol=ETHUSDT', '/api/market?symbol=BAD'):
            self.assertEqual(self.get(path)[0], 400)
        self.assertEqual(self.get('/api/market?symbol=BTCUSDT', {'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(self.get('/', {'Host': 'other.example'})[0], 403)

    def test_error_status(self):
        with patch.object(server.collector, 'collect', side_effect=market.MarketError('offline')):
            status, body = self.get('/api/market?symbol=BTCUSDT')
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)['error'], 'offline')


if __name__ == '__main__':
    unittest.main()
