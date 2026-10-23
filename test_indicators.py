import unittest

import indicators as ind


def bars(closes, spread=1.0, volume=10.0):
    return [{'open': str(c), 'high': str(c + spread), 'low': str(c - spread), 'close': str(c), 'volume': str(volume)}
            for c in closes]


def snapshot(closes):
    return {'candles': {'1h': bars(closes)}, 'ticker': {'price': '101'},
            'mark': {'markPrice': '100.5', 'indexPrice': '100', 'lastFundingRate': '-0.0001'},
            'openInterest': {'openInterest': '1000'}}


class IndicatorTests(unittest.TestCase):
    def test_sma_and_position(self):
        result = ind.interval_indicators(bars(list(range(1, 51))))
        self.assertEqual(result['sma20'], 40.5)
        self.assertEqual(result['sma50'], 25.5)
        self.assertNotIn('sma200', result)
        self.assertAlmostEqual(result['closeVsSma20Pct'], (50 / 40.5 - 1) * 100, places=3)

    def test_rsi_extremes_and_balance(self):
        self.assertEqual(ind.rsi([float(i) for i in range(30)]), 100.0)
        self.assertEqual(ind.rsi([5.0] * 30), 50.0)
        self.assertEqual(ind.rsi([float(30 - i) for i in range(30)]), 0.0)
        self.assertAlmostEqual(ind.rsi([100.0 + (i % 2) for i in range(31)]), 50, delta=5)
        self.assertIsNone(ind.rsi([1.0] * 14))

    def test_atr_constant_range(self):
        closes = [100.0] * 30
        self.assertAlmostEqual(ind.atr([c + 2 for c in closes], [c - 2 for c in closes], closes), 4.0)

    def test_volume_ratio_and_changes(self):
        data = bars([100.0] * 25)
        data[-1]['volume'] = '30'
        data[-1]['close'] = '110'
        result = ind.interval_indicators(data)
        self.assertEqual(result['volumeRatio20'], 3.0)
        self.assertEqual(result['change1Pct'], 10.0)

    def test_market_and_flatten(self):
        facts = ind.flatten(ind.compute(snapshot([100.0 + i for i in range(30)])))
        self.assertEqual(facts['market.fundingRatePct'], -0.01)
        self.assertEqual(facts['market.premiumPct'], 0.5)
        self.assertIn('1h.rsi14', facts)
        self.assertNotIn('1h.bars', facts)


if __name__ == '__main__':
    unittest.main()
