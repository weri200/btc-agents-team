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
        net, held = research.trade(1, 100, 2, up, 1, 1, 2, 0)
        self.assertEqual((round(net, 9), held), (0.02, 2))  # target 102 in the 2nd bar
        self.assertAlmostEqual(research.trade(-1, 100, 2, up, 1, 1, 2, 0)[0], -0.02)  # short: its stop is 102
        both = [bar(0, 97, 103, 100)]
        self.assertAlmostEqual(research.trade(1, 100, 2, both, 1, 1, 1, 0)[0], -0.02)  # touches both: counted as the stop
        flat = [bar(0, 99.5, 100.5, 100), bar(1, 99.5, 101, 100.8)]
        self.assertAlmostEqual(research.trade(1, 100, 2, flat, 1, 1, 2, 0.001)[0], 0.008 - 0.001)  # closes at the end, less fees

    def test_trailing_stop(self):
        # Long from 100 with a 2-point stop distance: the high of 106 lifts the stop to 104, hit in the third bar.
        path = [bar(0, 99, 103, 102), bar(1, 102, 106, 105), bar(2, 103, 105, 104), bar(3, 100, 110, 109)]
        net, held = research.trail_trade(1, 100, 1, path, 2, 4, 0)
        self.assertEqual((round(net, 9), held), (0.04, 3))
        self.assertEqual(research.trail_trade(1, 100, 1, [bar(0, 97.5, 101, 99)], 2, 1, 0)[1], 1)  # first-bar stop at 98
        self.assertAlmostEqual(research.trail_trade(1, 100, 1, [bar(0, 98.5, 101, 99)], 2, 1, 0)[0], -0.01)  # timeout at the close
        self.assertAlmostEqual(research.trail_trade(-1, 100, 1, [bar(0, 95, 101, 96), bar(1, 94, 98, 97)], 2, 2, 0)[0], 0.03)  # short: stop 97

    def test_solo_takes_one_position_at_a_time(self):
        trades = [(0, 0.10, 8), (4 * H, 0.50, 4), (8 * H, -0.10, 4)]  # the second opens while the first is still held
        result = research.solo(trades)
        self.assertEqual(result['trades'], 2)
        self.assertAlmostEqual(result['returnPct'], (1.1 * 0.9 - 1) * 100)
        self.assertAlmostEqual(result['maxDrawdownPct'], 10)
        self.assertEqual(research.solo([]), {'trades': 0, 'returnPct': 0.0, 'maxDrawdownPct': 0.0})

    def test_rules(self):
        up = {'1d.change20Pct': 3, '1d.closeVsSma50Pct': 2, '1d.closeVsSma200Pct': 5, '4h.closeVsSma20Pct': 1, '4h.change1Pct': 0.2,
              '4h.close': 110, '4h.high20': 105, '4h.low20': 90, '4h.rsi14': 40, '4h.volumeRatio20': 1.2, '4h.oiChange1Pct': 0.5,
              'market.fundingRatePct': 0.01}
        trend_rules = [r for r in research.RULES if not r.startswith(('cci', '4bc'))]
        self.assertEqual({r: research.RULES[r][1](up) for r in trend_rules}, dict.fromkeys(trend_rules, 1))
        self.assertEqual(research.cci_cross(up), 0)  # no CCI facts: never trades
        self.assertEqual(research.RULES['momentum-funding'][1]({**up, 'market.fundingRatePct': 0.05}), 0)  # crowded longs
        self.assertEqual(research.RULES['momentum-oi'][1]({**up, '4h.oiChange1Pct': -0.1}), 0)
        self.assertEqual(research.trend({**up, '1d.closeVsSma50Pct': -1}), 0)
        down = {**up, '1d.change20Pct': -3, '1d.closeVsSma50Pct': -2, '4h.closeVsSma20Pct': -1, '4h.change1Pct': -0.2,
                '4h.close': 85, '4h.rsi14': 60, 'market.fundingRatePct': -0.05}
        self.assertEqual(research.momentum(down), -1)
        self.assertEqual(research.breakout(down), -1)
        self.assertEqual(research.pullback(down), -1)
        self.assertEqual(research.RULES['momentum-funding'][1](down), 0)  # crowded shorts


class BandsTest(unittest.TestCase):
    def test_cci_and_bands_match_hand_calculation(self):
        bars = [bar(i, c, c, c) for i, c in enumerate(range(1, 22))]  # high = low = close, so typical price = close
        f = research.bands(bars)
        # Last 20 closes are 2..21: mean 11.5, mean deviation 5, population sd sqrt(399/12).
        self.assertAlmostEqual(f['bb.cci'], (21 - 11.5) / (0.015 * 5))
        self.assertAlmostEqual(f['bb.upper'], 11.5 + 2 * (399 / 12) ** 0.5)
        self.assertAlmostEqual(f['bb.lowerPrev'], 10.5 - 2 * (399 / 12) ** 0.5)
        self.assertEqual((f['bb.close'], f['bb.closePrev']), (21, 20))
        self.assertEqual(research.bands(bars[:20]), {})  # needs one bar more than the period for 'Prev'

    def test_bands_prefix_and_mid(self):
        f = research.bands([bar(i, c, c, c) for i, c in enumerate(range(1, 22))], prefix='bb1h')
        self.assertAlmostEqual(f['bb1h.mid'], 11.5)
        self.assertNotIn('bb.cci', f)

    def test_4bc_rules(self):
        up = {'bb.cciPrev': -130, 'bb.cci': -90}
        self.assertEqual([research.cci_cross(up), research.RULES['4bc'][1](up)], [1, 1])
        self.assertEqual(research.cci_cross({'bb.cciPrev': -90, 'bb.cci': -130}), 0)  # falling through -100 is no signal
        self.assertEqual(research.cci_cross({'bb.cciPrev': 130, 'bb.cci': 90}), -1)
        lower = {'bb1h.cciPrev': -60, 'bb1h.cci': -10, 'bb15m.cciPrev': 20, 'bb15m.cci': 50}  # both past -100 and rising
        self.assertEqual(research.four_bc_mtf({**up, **lower}), 1)
        self.assertEqual(research.four_bc_mtf(up), 0)  # no lower-frame facts: no trade
        self.assertEqual(research.four_bc_mtf({**up, **lower, 'bb15m.cci': 10}), 0)  # 15m CCI turning down
        self.assertEqual(research.four_bc_mtf({**up, **lower, 'bb1h.cci': -120, 'bb1h.cciPrev': -150}), 0)  # 1h still under -100
        down = {'bb.cciPrev': 130, 'bb.cci': 90, 'bb1h.cciPrev': 60, 'bb1h.cci': 10, 'bb15m.cciPrev': -20, 'bb15m.cci': -50}
        self.assertEqual(research.four_bc_mtf(down), -1)

    def test_band_trade(self):
        f = {'bb.lower': 95, 'bb.mid': 100, 'bb.upper': 105}
        path = [bar(0, 97, 100.5, 100), bar(1, 99, 105.5, 105), bar(2, 99, 100, 100)]
        net, held = research.band_trade(1, 97, f, path, 3, 0)
        self.assertAlmostEqual(net, ((100 / 97 - 1) + (105 / 97 - 1)) / 2)  # middle, then far band
        self.assertEqual(held, 2)  # out once both targets are hit
        self.assertAlmostEqual(research.band_trade(1, 97, f, [bar(0, 94, 101, 96)], 1, 0)[0], 95 / 97 - 1)  # stop and middle in one bar: stop
        stopped = [bar(0, 97, 100.5, 100), bar(1, 94, 99, 95)]
        self.assertAlmostEqual(research.band_trade(1, 97, f, stopped, 2, 0.001)[0], ((100 / 97 - 1) + (95 / 97 - 1)) / 2 - 0.001)  # rest stopped
        self.assertAlmostEqual(research.band_trade(1, 97, f, [bar(0, 96, 99, 98)], 1, 0)[0], 98 / 97 - 1)  # timeout at the close
        self.assertAlmostEqual(research.band_trade(-1, 103, f, [bar(0, 99.5, 104, 100)], 1, 0)[0], ((1 - 100 / 103) + (1 - 100 / 103)) / 2)  # short: rest at the close
        self.assertIsNone(research.band_trade(1, 94, f, path, 2, 0))  # entry under the stop band
        self.assertIsNone(research.band_trade(1, 101, f, path, 2, 0))  # entry already past the middle band


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
                data = research.research('BTCUSDT', 8, [1.0], [1.0], [12], 0, collector, now=NOW, folder=archive, log=lambda line: None,
                                         trails=[2.0], chart=('trend', 1.0, 1.0, 12))
        self.assertEqual(data['moments'], 48)
        self.assertTrue(seen and all(seen))
        trend = next(g for g in data['results'] if g['rule'] == 'trend')
        # A steady climb: the daily trend says long every time and the target is always reached first (no fees here;
        # the fake moves are too small next to the price to cover them).
        self.assertEqual(trend['shorts'], 0)
        self.assertGreater(trend['train']['trades'], 0)
        self.assertEqual((trend['train']['hitRate'], trend['test']['hitRate']), (1.0, 1.0))
        self.assertGreater(trend['test']['meanNetPct'], 0)
        self.assertEqual({g['tp'] for g in data['results'] if g['rule'] in research.BAND_EXITS}, {'밴드'})
        trailed = next(g for g in data['results'] if g['rule'] == 'trend' and g['tp'] == '추적')
        self.assertGreater(trailed['train']['trades'], 0)
        # 12h holds on signals every 4h: the solo replay takes one trade per 12 hours at most.
        self.assertLess(trend['train']['solo']['trades'], trend['train']['trades'])
        self.assertGreater(trend['train']['solo']['returnPct'], 0)
        charted = data['chart']['trades']
        # The chart holds the one-at-a-time trades of that group, each long with its target above and stop below the entry.
        self.assertEqual(len(charted), trend['train']['solo']['trades'] + trend['test']['solo']['trades'])
        at, net, held, direction, entry, lines = charted[0]
        self.assertEqual(direction, 1)
        self.assertGreater(lines['익절'], entry)
        self.assertLess(lines['손절'], entry)
        page = research.chart_page('BTCUSDT', 'trend', data['chart']['bars'], charted, 0)
        self.assertIn('"side": 1', page)
        self.assertNotIn('__DATA__', page)



D = 24 * H


def days(closes, start=1700000000000 - 1700000000000 % D):
    return [{'openTime': start + i * D, 'closeTime': start + (i + 1) * D - 1, 'close': str(c)} for i, c in enumerate(closes)]


class MomentumTest(unittest.TestCase):
    CLOSES = [100] * 20 + [110, 121, 108.9, 98.01]  # up 10%, up 10%, down 10%, down 10%

    def funding(self, bars):
        # One settlement inside the first held day and one inside the last.
        return [{'time': int(bars[20]['closeTime']) + 1, 'fundingRate': '0.001'}, {'time': int(bars[22]['closeTime']) + 5, 'fundingRate': '0.001'}]

    def test_holds_until_the_signal_flips_and_pays_fees_and_funding(self):
        bars = days(self.CLOSES)
        run = research.hold_until_flip(bars, self.funding(bars), 2, 0.0005)
        nets = [n for _, n in run['days']]
        # Long on day one (entry fee and funding paid), held on day two, flipped short on day three (two units of fee,
        # and a short receives positive funding).
        self.assertEqual(len(nets), 3)
        self.assertAlmostEqual(nets[0], 0.10 - 0.0005 - 0.001)
        self.assertAlmostEqual(nets[1], -0.10)
        self.assertAlmostEqual(nets[2], 0.10 - 0.001 + 0.001)
        self.assertAlmostEqual(run['fees'], 0.0015)
        self.assertAlmostEqual(run['funding'], 0.0)

    def test_always_long_benchmark_and_volatility_sizing(self):
        bars = days(self.CLOSES)
        hold = research.hold_until_flip(bars, self.funding(bars), None, 0.0005)
        self.assertAlmostEqual([n for _, n in hold['days']][2], -0.10 - 0.001)  # a long pays the funding
        sized = research.hold_until_flip(bars, [], 2, 0, vol_target=0.2)
        # Twenty daily changes, one of +10%: realized volatility about 41.6% a year, so the position is about 0.48.
        changes = [0.0] * 19 + [0.1]
        mean = sum(changes) / 20
        size = 0.2 / ((sum((c - mean) ** 2 for c in changes) / 20 * 365) ** 0.5)
        self.assertAlmostEqual(sized['days'][0][1], size * 0.10)
        # Up on a 30-day view after twenty flat days: no realized volatility, so the size is the cap.
        flat = research.hold_until_flip(days([90] * 11 + [100] * 30 + [110]), [], 30, 0, vol_target=0.2)
        self.assertAlmostEqual(flat['days'][-1][1], 2.0 * 0.10)

    def test_compound_by_year(self):
        new_year = 1767225600000  # 2026-01-01T00:00Z
        result = research.compound([(new_year - D, 0.10), (new_year, -0.5), (new_year + D, 0.10)])
        self.assertAlmostEqual(result['returnPct'], (1.1 * 0.5 * 1.1 - 1) * 100)
        self.assertAlmostEqual(result['maxDrawdownPct'], 50)
        self.assertEqual(set(result['years']), {'2025', '2026'})
        self.assertAlmostEqual(result['years']['2026'], (0.5 * 1.1 - 1) * 100)

    def test_tsmom_runs_on_exchange_history(self):
        collector = market.Collector(Path(tempfile.mkdtemp()) / 'm.sqlite3')
        with patch.object(collector, 'request', side_effect=fake_request):
            results = research.tsmom('BTCUSDT', NOW - 30 * D, [5, 10], 0.1, collector=collector, now=NOW, log=lambda line: None)
        self.assertEqual(list(results), ['5일', '10일', '계속 롱'])
        self.assertGreater(results['5일']['returnPct'], 0)  # prices only climb in the fake, so momentum stays long
        self.assertGreater(results['계속 롱']['funding'], 0)  # positive rates: the long paid

if __name__ == '__main__':
    unittest.main()
