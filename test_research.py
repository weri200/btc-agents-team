import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import market
import research
from test_backtest import day_csv

H = 3600000
NOW = 1792800000000 + 3 * 60000


def bar(t, low, high, close):
    return {'openTime': t, 'high': str(high), 'low': str(low), 'close': str(close)}


def fake_request(path, **params):
    """Prices climb one per hour; every bar has volume 10 with 4 taker buys, so taker ratios exist."""
    if path.endswith('fundingRate'):
        first = -(-params['startTime'] // (8 * H)) * 8 * H
        return [{'symbol': 'BTCUSDT', 'fundingTime': t, 'fundingRate': '0.0001'}
                for t in range(first, params['endTime'] + 1, 8 * H)][:params['limit']]
    step = market.INTERVALS[params['interval']]
    rows = []
    for t in range(-(-params['startTime'] // step) * step, params['endTime'] + 1, step)[:params['limit']]:  # bars open on their boundary
        p = 1000 + (t - 1700000000000) / H
        rows.append([t, f'{p:.4f}', f'{p + step / H:.4f}', f'{p - 0.5:.4f}', f'{p + step / H:.4f}', '10', t + step - 1, '1000', 5, '4', '400', '0'])
    return rows


class TradeTest(unittest.TestCase):
    def test_target_stop_both_and_timeout(self):
        up = [bar(0, 99.5, 100.5, 100), bar(1, 100, 102.1, 102)]
        self.assertAlmostEqual(research.trade(1, 100, 2, up, 1, 1, 2, 0), 0.02)  # target 102 in the 2nd bar
        self.assertAlmostEqual(research.trade(-1, 100, 2, up, 1, 1, 2, 0), -0.02)  # short: its stop is 102
        both = [bar(0, 97, 103, 100)]
        self.assertAlmostEqual(research.trade(1, 100, 2, both, 1, 1, 1, 0), -0.02)  # touches both: counted as the stop
        flat = [bar(0, 99.5, 100.5, 100), bar(1, 99.5, 101, 100.8)]
        self.assertAlmostEqual(research.trade(1, 100, 2, flat, 1, 1, 2, 0.001), 0.008 - 0.001)  # closes at the end, less fees

    def test_rules(self):
        up = {'1d.change20Pct': 3, '1d.closeVsSma50Pct': 2, '1d.closeVsSma200Pct': 5, '4h.closeVsSma20Pct': 1, '4h.change1Pct': 0.2,
              '4h.close': 110, '4h.high20': 105, '4h.low20': 90, '4h.rsi14': 40, '4h.volumeRatio20': 1.2, '4h.oiChange1Pct': 0.5,
              'market.fundingRatePct': 0.01}
        self.assertEqual({r: f(up) for r, (_, f) in research.RULES.items()}, dict.fromkeys(research.RULES, 1))
        self.assertEqual(research.RULES['momentum-funding'][1]({**up, 'market.fundingRatePct': 0.05}), 0)  # crowded longs
        self.assertEqual(research.RULES['momentum-oi'][1]({**up, '4h.oiChange1Pct': -0.1}), 0)
        self.assertEqual(research.trend({**up, '1d.closeVsSma50Pct': -1}), 0)
        down = {**up, '1d.change20Pct': -3, '1d.closeVsSma50Pct': -2, '4h.closeVsSma20Pct': -1, '4h.change1Pct': -0.2,
                '4h.close': 85, '4h.rsi14': 60, 'market.fundingRatePct': -0.05}
        self.assertEqual(research.momentum(down), -1)
        self.assertEqual(research.breakout(down), -1)
        self.assertEqual(research.pullback(down), -1)
        self.assertEqual(research.RULES['momentum-funding'][1](down), 0)  # crowded shorts


class ResearchTest(unittest.TestCase):
    def test_end_to_end_on_fake_market(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'archive'
            (archive / 'BTCUSDT').mkdir(parents=True)
            for at in range(NOW - 15 * 24 * H, NOW + 24 * H, 24 * H):
                day = market.iso(at)[:10]
                (archive / 'BTCUSDT' / f'{day}.csv').write_text(day_csv(day), encoding='utf-8')
            collector = market.Collector(Path(folder) / 'm.sqlite3')
            seen = []
            real_facts = research.facts_at

            def facts(symbol, at, bars, closes, funding, folder):
                result = real_facts(symbol, at, bars, closes, funding, folder)
                n = {i: research.bisect_left(closes[i], at) for i in research.FRAMES}
                seen.append(all(closes[i][n[i] - 1] < at for i in research.FRAMES))
                return result
            with patch.object(collector, 'request', side_effect=fake_request), patch('research.facts_at', side_effect=facts):
                data = research.research('BTCUSDT', 8, [1.0], [1.0], [12], 0, collector, now=NOW, folder=archive, log=lambda line: None)
        self.assertEqual(data['moments'], 48)
        self.assertTrue(seen and all(seen))
        trend = next(g for g in data['results'] if g['rule'] == 'trend')
        # A steady climb: the daily trend says long every time and the target is always reached first (no fees here;
        # the fake moves are too small next to the price to cover them).
        self.assertEqual(trend['shorts'], 0)
        self.assertGreater(trend['train']['trades'], 0)
        self.assertEqual((trend['train']['hitRate'], trend['test']['hitRate']), (1.0, 1.0))
        self.assertGreater(trend['test']['meanNetPct'], 0)


if __name__ == '__main__':
    unittest.main()
