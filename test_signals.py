import unittest

import indicators
import research
import signals


def facts(**changes):
    """An up-trend 4h close above its 20-bar high on heavy volume: a swing long."""
    base = {'1d.change20Pct': 3.0, '1d.closeVsSma50Pct': 1.0, '4h.close': 110.0, '4h.high20': 105.0, '4h.low20': 90.0,
            '4h.volumeRatio20': 1.5, '4h.atr14': 2.0,
            '1h.change20Pct': 1.0, '1h.closeVsSma50Pct': -0.5, '15m.close': 100.0, '15m.high20': 101.0, '15m.low20': 99.0,
            '15m.volumeRatio20': 2.0}
    return {**base, **changes}


class SignalsTest(unittest.TestCase):
    def test_swing_needs_trend_breakout_and_volume(self):
        self.assertEqual(signals.decide(facts(), 'swing'), 'long')
        self.assertEqual(signals.decide(facts(**{'4h.volumeRatio20': 1.0}), 'swing'), 'wait')
        self.assertEqual(signals.decide(facts(**{'4h.close': 104.0}), 'swing'), 'wait')
        self.assertEqual(signals.decide(facts(**{'1d.closeVsSma50Pct': -1.0}), 'swing'), 'wait')  # trend mixed
        down = facts(**{'1d.change20Pct': -3.0, '1d.closeVsSma50Pct': -1.0, '4h.close': 89.0})
        self.assertEqual(signals.decide(down, 'swing'), 'short')
        self.assertEqual(signals.decide(facts(**{'4h.close': None}), 'swing'), 'wait')

    def test_research_scores_the_same_swing_rule(self):
        for f in (facts(), facts(**{'4h.volumeRatio20': 0.5}), facts(**{'1d.change20Pct': -1.0})):
            self.assertEqual(research.RULES['breakout-volume'][1](f), signals.direction(f, 'swing'))

    def test_checks_and_exit_plan_show_the_values(self):
        lines = signals.checks(facts(**{'4h.volumeRatio20': 0.18}), 'swing')
        self.assertEqual(len(lines), 3)
        self.assertIn('위', lines[0])
        self.assertIn('고점 돌파', lines[1])
        self.assertIn('0.18배 (부족)', lines[2])
        self.assertIn('자료 없음', signals.checks({}, 'swing')[1])
        # The explainer's wording of the volume ratio draws the same line as the rule.
        for ratio, word in ((1.2, '평소보다 많음'), (1.0, '평소 이하')):
            self.assertIn('충분' if ratio > 1 else '부족', signals.checks(facts(**{'4h.volumeRatio20': ratio}), 'swing')[2])
            self.assertIn(word, indicators.describe({'4h.volumeRatio20': ratio}, ['4h.volumeRatio20'])[0])
        self.assertIn('진입 100 · 익절 104 · 손절 98', signals.exit_plan(facts(), 'swing', 'long'))  # 15m close, 4h ATR
        self.assertIn('익절 96 · 손절 102', signals.exit_plan(facts(), 'swing', 'short'))
        self.assertNotIn('익절 1', signals.exit_plan(facts(), 'swing', 'wait'))
        self.assertEqual(list(signals.FRAMES), ['swing'])  # the intraday call was dropped in rules-v2


if __name__ == '__main__':
    unittest.main()
