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


    def test_derivatives_history(self):
        rows = lambda **fields: [dict(time=i, **{k: str(f(i)) for k, f in fields.items()}) for i in range(25)]
        history = {'funding': [{'time': i, 'fundingRate': rate} for i, rate in enumerate(['0.0001', '-0.0001', '0.0003'])],
                   'periods': {'4h': {'openInterest': rows(sumOpenInterest=lambda i: 100 + i, sumOpenInterestValue=lambda i: 1e6),
                                      'globalLongShort': rows(longShortRatio=lambda i: 1 + i / 100),
                                      'topLongShort': rows(longShortRatio=lambda i: 2.0),
                                      'taker': rows(buySellRatio=lambda i: 1.0 if i < 24 else 1.24)}}}
        result = ind.derivatives_indicators(history)
        self.assertEqual(result['market']['fundingAvgPct'], 0.01)
        self.assertEqual(result['market']['fundingPositiveShare'], 0.667)
        values = result['intervals']['4h']
        self.assertEqual(values['oiChange24Pct'], 24.0)
        self.assertEqual(values['oiChange1Pct'], round((124 / 123 - 1) * 100, 3))
        self.assertEqual(values['longShortRatioChange24'], 0.24)
        self.assertEqual(values['topLongShortRatioChange24'], 0.0)
        self.assertEqual(values['takerBuySellRatio'], 1.24)
        self.assertEqual(values['takerBuySellAvg24'], 1.01)

    def test_compute_merges_derivatives_under_interval_keys(self):
        data = snapshot([100.0 + i for i in range(30)])
        data['derivatives'] = {'funding': [{'time': 0, 'fundingRate': '0.0001'}],
                               'periods': {'1h': {'openInterest': [{'time': 0, 'sumOpenInterest': '5', 'sumOpenInterestValue': '9'}],
                                                  'globalLongShort': [{'time': 0, 'longShortRatio': '1.2'}],
                                                  'topLongShort': [{'time': 0, 'longShortRatio': '1.1'}],
                                                  'taker': [{'time': 0, 'buySellRatio': '0.9'}]}}}
        facts = ind.flatten(ind.compute(data))
        self.assertEqual(facts['1h.longShortRatio'], 1.2)
        self.assertIn('1h.rsi14', facts)
        self.assertEqual(facts['market.fundingAvgPct'], 0.01)


    def test_describe_directions(self):
        facts = {'1d.closeVsSma200Pct': 11.79, '1h.closeVsSma20Pct': -0.5, '1h.rsi14': 75.0, '4h.oiChange1Pct': -0.548,
                 '1h.takerBuySellRatio': 0.8, '1h.takerBuySellAvg24': 1.0, 'market.fundingRatePct': -0.01}
        lines = ind.describe(facts, list(facts))
        self.assertIn('1d 종가는 SMA200보다 11.79% 위', lines)
        self.assertIn('1h 종가는 SMA20보다 0.5% 아래', lines)
        self.assertIn('1h RSI14 75.0: 과매수(70 이상)', lines)
        self.assertIn('4h 미결제약정 1기간 변화 -0.548%: 감소', lines)
        self.assertIn('1h 테이커 매수/매도 비율 0.8: 시장가 매도 우위, 24기간 평균 1.0보다 낮음', lines)
        self.assertIn('현재 펀딩비율 -0.01%: 숏이 롱에 지불', lines)


if __name__ == '__main__':
    unittest.main()
