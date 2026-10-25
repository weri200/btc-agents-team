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
    if path.endswith('/fundingRate'):
        return [{'symbol': symbol, 'fundingTime': NOW - (3 - i) * 28800000, 'fundingRate': f'{(-1) ** i * 0.0001:.4f}', 'markPrice': '100'}
                for i in range(3)]
    if path.startswith('/futures/data/'):
        step = market.HISTORY_PERIODS[params['period']]
        start = NOW // step * step - 25 * step
        history = []
        for i in range(25):
            row = {'symbol': symbol, 'timestamp': start + i * step, 'sumOpenInterest': str(1000 + i), 'sumOpenInterestValue': str(100000 + i),
                   'longShortRatio': '1.5', 'longAccount': '0.6', 'shortAccount': '0.4', 'buySellRatio': '1.1', 'buyVol': '11', 'sellVol': '10'}
            if 'taker' in path:
                del row['symbol']
            history.append(row)
        return history
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
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='snapshots'").fetchone())
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

    def test_derivatives_history(self):
        with patch.object(self.collector, 'request', side_effect=response) as request:
            history = self.collector.derivatives('BTCUSDT')
        self.assertEqual(request.call_count, 2 + 4 * len(market.HISTORY_PERIODS))
        self.assertEqual(len(history['funding']), 3)
        self.assertEqual(set(history['periods']['4h']), set(market.HISTORY_ENDPOINTS))
        self.assertEqual(history['warnings'], [])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM derivatives').fetchone()[0], 3 + 25 * 8)

    def test_derivatives_rejects_bad_history(self):
        for mutate in (lambda rows: rows.reverse(), lambda rows: rows[0].__setitem__('sumOpenInterest', '-1'),
                       lambda rows: rows[0].__setitem__('symbol', 'ETHUSDT'), lambda rows: rows.clear()):
            def bad(path, **params):
                data = response(path, **params)
                if path.endswith('openInterestHist'):
                    mutate(data)
                return data
            with self.subTest(mutate=mutate), patch.object(self.collector, 'request', side_effect=bad),                     self.assertRaises(market.MarketError):
                self.collector.derivatives('BTCUSDT')

    def test_stale_derivatives_flagged(self):
        def stale(path, **params):
            data = response(path, **params)
            if path.endswith('takerlongshortRatio') and params['period'] == '1h':
                for row in data:
                    row['timestamp'] -= 10 * 3600000
            return data
        with patch.object(self.collector, 'request', side_effect=stale):
            history = self.collector.derivatives('BTCUSDT')
        self.assertTrue(any('1h taker' in w for w in history['warnings']))

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

    def test_scores_endpoint(self):
        with patch.object(server.journal, 'scores', return_value=[{'role': 'judge'}]):
            data = json.loads(self.get('/api/scores')[1])
        self.assertEqual((data['groups'], data['brackets']['swing']), ([{'role': 'judge'}], {'target': 2.0, 'stop': 1.0}))

    def test_backtest_endpoints(self):
        with patch.object(server.journal, 'backtests', return_value={'total': 0, 'reports': []}):
            self.assertEqual(json.loads(self.get('/api/backtests')[1]), {'total': 0, 'reports': []})
        with patch.object(server.journal, 'backtest_report', side_effect=lambda rid, path: {'id': rid} if rid == 'a' else None):
            self.assertEqual(json.loads(self.get('/api/backtests?id=a')[1]), {'id': 'a'})
            self.assertEqual(self.get('/api/backtests?id=b')[0], 404)
        for path in ('/api/backtests?id=', '/api/backtests?id=a&id=b', '/api/backtests?x=1', '/api/backtests?id=' + 'a' * 65):
            self.assertEqual(self.get(path)[0], 400)

    def test_error_status(self):
        with patch.object(server.collector, 'collect', side_effect=market.MarketError('offline')):
            status, body = self.get('/api/market?symbol=BTCUSDT')
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)['error'], 'offline')

    def post(self, body='{"symbol":"BTCUSDT"}', headers=None):
        connection = HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        connection.request('POST', '/api/analyze', body, {'Content-Type': 'application/json', **(headers or {})})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def assert_released(self):
        # The handler answers before its finally block releases the lock, so give it a moment.
        self.assertTrue(server.local_llm.lock.acquire(timeout=1))
        server.local_llm.lock.release()

    def test_local_analysis_guards(self):
        self.assertEqual(self.post(headers={'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(self.post(headers={'Content-Type': 'text/plain'})[0], 400)
        self.assertEqual(self.post('{"symbol":"BAD"}')[0], 400)
        self.assertEqual(self.post('null')[0], 400)
        with server.local_llm.lock:
            self.assertEqual(self.post()[0], 409)

    def test_local_analysis_success_and_failure(self):
        with patch.object(server.local_llm, 'load_config', return_value={'model': 'm', 'temperature': 0.1}), \
                patch.object(server.collector, 'collect', return_value={'serverTime': NOW}), \
                patch.object(server.collector, 'derivatives', return_value={}), \
                patch.object(server.macro, 'collect', return_value={}), patch.object(server.news, 'collect', return_value={'items': [], 'warnings': []}) as news_feed, \
                patch.object(server.local_llm, 'analyze', return_value={'id': 'test'}),                 patch.object(server.journal, 'save_report') as save:
            status, body = self.post()
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['id'], 'test')
            save.assert_called_once_with({'id': 'test'}, server.collector.db_path)
            news_feed.assert_called_once_with(NOW)  # headlines are fetched for the explainer
        # A journal that cannot save still returns the finished report.
        with patch.object(server.local_llm, 'load_config', return_value={'model': 'm', 'temperature': 0.1}),                 patch.object(server.collector, 'collect', return_value={'serverTime': NOW}),                 patch.object(server.collector, 'derivatives', return_value={}),                 patch.object(server.macro, 'collect', return_value={}), patch.object(server.news, 'collect', return_value={'items': [], 'warnings': []}) as news_feed,                 patch.object(server.local_llm, 'analyze', return_value={'id': 'test'}),                 patch.object(server.journal, 'save_report', side_effect=sqlite3.OperationalError('locked')):
            self.assertEqual(self.post()[0], 200)
        with patch.object(server.collector, 'collect', side_effect=market.MarketError('offline')):
            self.assertEqual(self.post()[0], 502)
        # A bad models.json still gives the code's calls, without the explanation.
        with patch.object(server.local_llm, 'load_config', side_effect=server.local_llm.AnalysisError('models.json 오류.')),                 patch.object(server.collector, 'collect', return_value={'serverTime': NOW}),                 patch.object(server.collector, 'derivatives', return_value={}),                 patch.object(server.macro, 'collect', return_value={}), patch.object(server.news, 'collect', return_value={'items': [], 'warnings': []}) as news_feed,                 patch.object(server.local_llm, 'analyze', return_value={'id': 'test', 'warnings': []}) as analyze,                 patch.object(server.journal, 'save_report'):
            status, body = self.post()
        self.assertEqual(status, 200)
        self.assertIsNone(analyze.call_args.args[1])
        news_feed.assert_not_called()  # no explainer, so no headlines to fetch
        self.assertIn('해설 없이 규칙 판정만', json.loads(body)['warnings'][0])
        self.assert_released()

    def test_progress_and_last_report_endpoints(self):
        progress = server.local_llm.Progress(Path(tempfile.mkdtemp()) / 'timings.json')
        with patch.object(server.local_llm, 'progress', progress):
            self.assertEqual(json.loads(self.get('/api/progress')[1]), {'state': 'idle'})
            self.assertEqual(self.get('/api/last-report')[0], 404)
            with patch.object(server.local_llm, 'load_config', return_value={'model': 'm', 'temperature': 0.1}), \
                    patch.object(server.collector, 'collect', return_value={'serverTime': NOW}), \
                    patch.object(server.collector, 'derivatives', return_value={}), \
                    patch.object(server.macro, 'collect', return_value={}), patch.object(server.news, 'collect', return_value={'items': [], 'warnings': []}) as news_feed, \
                    patch.object(server.local_llm, 'analyze', return_value={'id': 'done-report'}),                     patch.object(server.journal, 'save_report'):
                self.assertEqual(self.post()[0], 200)
            state = json.loads(self.get('/api/progress')[1])
            self.assertEqual((state['state'], state['reportId']), ('done', 'done-report'))
            self.assertEqual(json.loads(self.get('/api/last-report')[1])['id'], 'done-report')
            with patch.object(server.collector, 'collect', side_effect=RuntimeError('boom')):
                self.assertEqual(self.post()[0], 500)
            self.assertEqual(json.loads(self.get('/api/progress')[1])['state'], 'failed')
            self.assert_released()


if __name__ == '__main__':
    unittest.main()
