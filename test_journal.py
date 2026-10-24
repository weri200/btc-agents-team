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


def report(rid, as_of, intraday, swing, version='v-test'):
    def horizon(verdict):
        return {'verdict': verdict, 'agents': [{'id': 'technical', 'verdict': 'long'}]}
    stamp = datetime.fromtimestamp(as_of / 1000, timezone.utc).isoformat(timespec='milliseconds')
    return {'id': rid, 'symbol': 'BTCUSDT', 'asOf': stamp, 'promptVersion': version,
            'horizons': {'intraday': horizon(intraday), 'swing': horizon(swing)}}


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'm.sqlite3'
        journal.connect(self.path).close()

    def tearDown(self):
        self.dir.cleanup()

    def bars(self, interval, closes):
        """closes: {close time: price}; stores the bar that closes at that time."""
        step = journal.INTERVALS[interval]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS candles (symbol TEXT, interval TEXT, open_time INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,open_time))')
            db.executemany('INSERT INTO candles VALUES (?,?,?,?)',
                           [('BTCUSDT', interval, at - step, json.dumps({'close': str(price)})) for at, price in closes.items()])

    def group(self, groups, horizon, role='judge'):
        return next(g for g in groups if g['horizon'] == horizon and g['role'] == role)

    def test_scores_long_short_wait_pending_and_missing(self):
        # Intraday: entry 100 at T0, 4h later 110. Long wins +10%; short loses.
        self.bars('15m', {T0: 100, T0 + 4 * H: 110})
        # Swing: entry 100 at T0, 72h later 90. Short wins +10%.
        self.bars('4h', {T0: 100, T0 + 72 * H: 90})
        journal.save_report(report('a', T0, 'long', 'short'), self.path)
        journal.save_report(report('b', T0 + 7 * 60000, 'short', 'wait'), self.path)  # mid-bar decision uses the bar closed before it
        journal.save_report(report('c', T0 + 8 * H, 'long', 'long'), self.path)  # no candles stored then
        journal.save_report(report('d', T0 + 100 * H, 'long', 'long'), self.path)  # not yet due
        groups = journal.scores(self.path, now=T0 + 101 * H)

        intraday = self.group(groups, 'intraday')
        self.assertEqual((intraday['calls'], intraday['long'], intraday['short'], intraday['wait']), (4, 3, 1, 0))
        self.assertEqual((intraday['scored'], intraday['hits'], intraday['missing'], intraday['pending']), (2, 1, 1, 1))
        self.assertAlmostEqual(intraday['meanReturnPct'], 0.0)
        swing = self.group(groups, 'swing')
        self.assertEqual((swing['scored'], swing['hits'], swing['wait'], swing['pending'], swing['missing']), (1, 1, 1, 1, 1))
        self.assertAlmostEqual(swing['meanReturnPct'], 10.0)
        self.assertAlmostEqual(swing['waitRate'], 0.25)
        # Team members are scored separately from the judge.
        self.assertEqual(self.group(groups, 'swing', 'technical')['long'], 4)

    def test_saving_the_same_report_twice_counts_once(self):
        journal.save_report(report('a', T0, 'wait', 'wait'), self.path)
        journal.save_report(report('a', T0, 'wait', 'wait'), self.path)
        group = self.group(journal.scores(self.path, now=T0), 'intraday')
        self.assertEqual((group['calls'], group['hitRate'], group['meanReturnPct']), (1, None, None))


if __name__ == '__main__':
    unittest.main()
