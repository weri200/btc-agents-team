from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import journal

H = 3600000
T0 = 1792800000000  # a multiple of 4h, so 15m and 4h bars line up with it


def report(rid, as_of, intraday, swing, version='v-test', atr=None):
    def horizon(verdict):
        return {'verdict': verdict, 'agents': [{'id': 'rule', 'verdict': verdict}]}
    stamp = datetime.fromtimestamp(as_of / 1000, timezone.utc).isoformat(timespec='milliseconds')
    return {'id': rid, 'symbol': 'BTCUSDT', 'asOf': stamp, 'promptVersion': version, 'facts': {'4h.atr14': atr} if atr else {},
            'horizons': {'intraday': horizon(intraday), 'swing': horizon(swing)}}


def flat_4h(start, price=100):
    """A 4h close of `price` at `start` (the entry) and at every 4h close of the 7-day swing hold after it."""
    return {start + i * 4 * H: price for i in range(43)}


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'm.sqlite3'
        journal.connect(self.path).close()

    def tearDown(self):
        self.dir.cleanup()

    def bars(self, interval, closes):
        """closes: {close time: price or (low, high, close)}; stores the bar that closes at that time."""
        step = journal.INTERVALS[interval]
        rows = []
        for at, value in closes.items():
            low, high, close = value if isinstance(value, tuple) else (value, value, value)
            rows.append(('BTCUSDT', interval, at - step, json.dumps({'low': str(low), 'high': str(high), 'close': str(close)})))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS candles (symbol TEXT, interval TEXT, open_time INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,open_time))')
            db.executemany('INSERT OR REPLACE INTO candles VALUES (?,?,?,?)', rows)

    def group(self, groups, horizon, role='judge'):
        return next(g for g in groups if g['horizon'] == horizon and g['role'] == role)

    def test_scores_long_short_wait_pending_and_missing(self):
        # Intraday: entry 100 at T0, 4h later 110. Long wins +10%; short loses.
        self.bars('15m', {T0: 100, T0 + 4 * H: 110})
        # Swing: entry 100 at T0 with a 4h ATR of 5. A bar dipping to 89 reaches the short's target at 90 (+10%) before its stop at 105.
        self.bars('4h', {**flat_4h(T0), T0 + 40 * H: (89, 101, 95)})
        journal.save_report(report('a', T0, 'long', 'short', atr=5), self.path)
        journal.save_report(report('b', T0 + 7 * 60000, 'short', 'wait'), self.path)  # mid-bar decision uses the bar closed before it
        journal.save_report(report('c', T0 + 8 * H, 'long', 'long', atr=5), self.path)  # no 15m candles then
        journal.save_report(report('d', T0 + 166 * H, 'long', 'long'), self.path)  # not yet due
        groups = journal.scores(self.path, now=T0 + 169 * H)

        intraday = self.group(groups, 'intraday')
        self.assertEqual((intraday['calls'], intraday['long'], intraday['short'], intraday['wait']), (4, 3, 1, 0))
        self.assertEqual((intraday['scored'], intraday['hits'], intraday['missing'], intraday['pending']), (2, 1, 1, 1))
        self.assertAlmostEqual(intraday['meanReturnPct'], 0.0)
        self.assertAlmostEqual(intraday['score'], 0.0)  # one hit +1, one miss -1
        swing = self.group(groups, 'swing')
        self.assertEqual((swing['scored'], swing['hits'], swing['wait'], swing['pending'], swing['missing']), (1, 1, 1, 2, 0))  # c's 7-day hold is not over
        self.assertAlmostEqual(swing['meanReturnPct'], 10.0)
        self.assertAlmostEqual(swing['waitRate'], 0.25)
        self.assertAlmostEqual(swing['score'], (1 + journal.POINTS['wait']) / 2)  # a hit and a finished wait; missing and pending left out
        self.assertLess(journal.POINTS['miss'], journal.POINTS['wait'])
        self.assertLess(journal.POINTS['wait'], 0)
        self.assertEqual({g['role'] for g in groups}, {'judge'})  # only the final call is saved

    def test_swing_exits_at_the_atr_target_or_stop(self):
        self.bars('4h', {**flat_4h(T0), T0 + 12 * H: (94, 103, 100)})
        with closing(journal.connect(self.path)) as db:
            def out(verdict, atr):
                return journal.outcome(db, 'BTCUSDT', T0, 'swing', verdict, T0 + 168 * H, atr)
            self.assertAlmostEqual(out('long', 5)['return'], -0.05)  # the low of 94 passes the stop at 95
            self.assertAlmostEqual(out('short', 2)['return'], -0.02)  # touches the target at 96 and the stop at 102: counted as the stop
            self.assertEqual((out('long', 10)['exit'], out('long', 10)['status']), (100, 'miss'))  # neither level: the close when the hold ends
            self.assertEqual(out('long', None), {'status': 'missing'})  # reports without an ATR cannot be scored
        # Backtest reports carry the ATR of prices times priceScale; scoring divides it back to real prices (10 / 2 = 5).
        journal.save_report({**report('s', T0, 'wait', 'long', atr=10), 'priceScale': 2}, self.path)
        self.assertAlmostEqual(self.group(journal.scores(self.path, now=T0 + 168 * H), 'swing')['meanReturnPct'], -5.0)

    def test_saving_the_same_report_twice_counts_once(self):
        journal.save_report(report('a', T0, 'wait', 'wait'), self.path)
        journal.save_report(report('a', T0, 'wait', 'wait'), self.path)
        group = self.group(journal.scores(self.path, now=T0), 'intraday')
        self.assertEqual((group['calls'], group['pending'], group['hitRate'], group['meanReturnPct'], group['score']), (1, 1, None, None, None))
        self.assertEqual(self.group(journal.scores(self.path, now=T0 + 4 * H), 'intraday')['score'], journal.POINTS['wait'])


if __name__ == '__main__':
    unittest.main()
