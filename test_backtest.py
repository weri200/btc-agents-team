from contextlib import closing
import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import backtest
import journal
import market

H = 3600000
NOW = 1792800000000 + 3 * 60000  # a few minutes past a 4h boundary
HEADER = ','.join(backtest.METRIC_COLUMNS)


def price(open_time):
    return f'{100 + (open_time - 1700000000000) / H:.4f}'  # rises one per hour, so every long wins


def klines(interval, limit, end):
    step = market.INTERVALS[interval]
    last = (end + 1) // step * step - step
    return [[t, price(t), price(t + step), price(t), price(t + step), '10', t + step - 1, '1000', 5, '4', '400', '0']
            for t in range(last - (limit - 1) * step, last + 1, step)]


def request(path, **params):
    if 'interval' in params:
        return klines(params['interval'], params['limit'], params['endTime'])
    last = params['endTime'] // (8 * H) * (8 * H)
    return [{'symbol': 'BTCUSDT', 'fundingTime': last - i * 8 * H, 'fundingRate': '0.0001'} for i in range(params['limit'])][::-1]


def day_csv(day):
    start = int(backtest.datetime.fromisoformat(day + 'T00:00:00+00:00').timestamp() * 1000)
    rows = [f"{backtest.datetime.fromtimestamp((start + i * 300000) / 1000, backtest.timezone.utc):%Y-%m-%d %H:%M:%S},BTCUSDT,"
            f"{1000 + i},{100000 + i},1.7,1.8,1.6,0.9" for i in range(288)]
    return '\n'.join([HEADER, *rows])


def report(snapshot, config):
    def horizon():
        return {'verdict': 'long', 'agents': [{'id': 'technical', 'verdict': 'short'}]}
    return {'id': f"r{snapshot['serverTime']}", 'symbol': snapshot['symbol'], 'asOf': market.iso(snapshot['serverTime']),
            'mode': 'analysis', 'promptVersion': 'v-test', 'horizons': {'intraday': horizon(), 'swing': horizon()}}


class BacktestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.collector = market.Collector(Path(self.dir.name) / 'm.sqlite3')
        self.archive = Path(self.dir.name) / 'archive'
        (self.archive / 'BTCUSDT').mkdir(parents=True)
        for at in range(NOW - 20 * 24 * H, NOW, 24 * H):
            day = market.iso(at)[:10]
            (self.archive / 'BTCUSDT' / f'{day}.csv').write_text(day_csv(day), encoding='utf-8')

    def run_backtest(self, limit=None):
        with patch.object(self.collector, 'request', side_effect=request), \
                patch('backtest.local_llm.analyze', side_effect=report) as analyze, \
                patch('backtest.local_llm.PROMPT_VERSION', 'v-test'):
            saved = backtest.run('BTCUSDT', 10, 4, limit, self.collector, config={}, now=NOW, folder=self.archive, log=lambda line: None)
        return saved, analyze

    def test_moments_newest_first_inside_the_scorable_range(self):
        moments = backtest.moments(NOW, 4 * H, 10)
        self.assertTrue(all(m % backtest.STEP == 0 for m in moments))
        self.assertEqual(moments, sorted(moments, reverse=True))
        self.assertLessEqual(moments[0], NOW - 72 * H)
        self.assertGreater(moments[-1], NOW - 10 * 24 * H)

    def test_scale_is_repeatable_and_far_from_one(self):
        scales = [backtest.scale_of(at) for at in range(0, 400 * backtest.STEP, backtest.STEP)]
        self.assertEqual(scales[:5], [backtest.scale_of(at) for at in range(0, 5 * backtest.STEP, backtest.STEP)])
        self.assertTrue(all(0.5 <= s <= 2 and abs(s - 1) > 0.09 for s in scales))

    def test_models_see_earlier_data_scaled_and_undated_while_scoring_uses_real_prices(self):
        saved, analyze = self.run_backtest(limit=3)
        self.assertEqual(saved, 3)
        for call in analyze.call_args_list:
            sent = call.args[0]
            at, scale = sent['serverTime'], backtest.scale_of(sent['serverTime'])
            self.assertTrue(sent['hideDate'])
            self.assertTrue(all(b['closeTime'] < at for bars in sent['candles'].values() for b in bars))
            self.assertTrue(all(r['time'] < at for r in sent['derivatives']['funding']))
            self.assertTrue(all(r['time'] < at for m in sent['derivatives']['periods'].values() for rows in m.values() for r in rows))
            last = sent['candles']['5m'][-1]
            self.assertAlmostEqual(float(last['close']), float(price(last['openTime'] + 300000)) * scale, places=4)
            self.assertAlmostEqual(float(sent['ticker']['price']), float(last['close']))
            self.assertEqual(sent['derivatives']['periods']['1h']['taker'][-1]['buySellRatio'], '0.6667')
            self.assertEqual(sent['openInterest']['openInterest'], str(1000 + (at - 300000) % (24 * H) // 300000))  # the row 5 minutes before
            self.assertIn('재현할 수 없어', sent['macro']['error'])

        with closing(sqlite3.connect(self.collector.db_path)) as db:
            self.assertEqual({r[0] for r in db.execute("SELECT json_extract(payload,'$.priceScale') FROM reports")},
                             {backtest.scale_of(c.args[0]['serverTime']) for c in analyze.call_args_list})
        groups = journal.scores(self.collector.db_path, now=NOW)
        judge = next(g for g in groups if g['role'] == 'judge' and g['horizon'] == 'swing')
        self.assertEqual((judge['mode'], judge['calls'], judge['scored'], judge['hits'], judge['missing']), ('backtest', 3, 3, 3, 0))
        technical = next(g for g in groups if g['role'] == 'technical' and g['horizon'] == 'intraday')
        self.assertEqual((technical['scored'], technical['hits']), (3, 0))

    def test_rerun_skips_moments_already_done(self):
        self.run_backtest(limit=2)
        saved, _ = self.run_backtest(limit=2)
        self.assertEqual(saved, 2)
        with closing(sqlite3.connect(self.collector.db_path)) as db:
            self.assertEqual(db.execute('SELECT count(DISTINCT as_of) FROM reports').fetchone()[0], 4)

    def test_missing_archive_day_skips_the_moment(self):
        for path in (self.archive / 'BTCUSDT').iterdir():
            path.unlink()
        missing = HTTPError('https://data.binance.vision/x', 404, 'Not Found', {}, None)
        with patch('backtest.urlopen', side_effect=missing):
            saved, analyze = self.run_backtest(limit=2)
        self.assertEqual((saved, analyze.call_count), (0, 0))
        self.assertEqual(journal.scores(self.collector.db_path, now=NOW), [])

    def test_archive_download_is_kept_and_checked(self):
        day = '2026-01-02'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr(f'BTCUSDT-metrics-{day}.csv', day_csv(day))

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False
        with patch('backtest.urlopen', return_value=Response(buffer.getvalue())):
            rows = backtest.metrics_day('BTCUSDT', day, self.archive)
        self.assertEqual(len(rows), 288)
        self.assertTrue((self.archive / 'BTCUSDT' / f'{day}.csv').exists())
        (self.archive / 'BTCUSDT' / '2026-01-03.csv').write_text('time,price\n1,2', encoding='utf-8')
        with self.assertRaises(market.MarketError):
            backtest.metrics_day('BTCUSDT', '2026-01-03', self.archive)


class JournalMigrationTest(unittest.TestCase):
    def test_reports_saved_before_backtests_count_as_live(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'm.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE reports (id TEXT PRIMARY KEY, symbol TEXT, as_of INTEGER, prompt_version TEXT, payload TEXT)')
                db.execute("INSERT INTO reports VALUES ('old','BTCUSDT',1,'v1','{}')")
                db.execute('CREATE TABLE verdicts (report_id TEXT, horizon TEXT, role TEXT, verdict TEXT, PRIMARY KEY(report_id,horizon,role))')
                db.execute("INSERT INTO verdicts VALUES ('old','swing','judge','wait')")
            self.assertEqual(journal.scores(path, now=2)[0]['mode'], 'analysis')


if __name__ == '__main__':
    unittest.main()
