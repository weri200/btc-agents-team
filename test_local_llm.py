import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import indicators
import local_llm as llm
import signals

CONFIG = {'model': 'model-a', 'temperature': 0.1}


def make_snapshot():
    def bars(n):
        return [{'closeTime': i, 'open': str(100 + i), 'high': str(102 + i), 'low': str(99 + i),
                 'close': str(101 + i), 'volume': '10'} for i in range(n)]
    snapshot = {'symbol': 'BTCUSDT', 'serverTime': 1780000000000, 'warnings': ['시각 주의'],
                'ticker': {'price': '160'}, 'mark': {'markPrice': '160.5', 'indexPrice': '160', 'lastFundingRate': '0.0001'},
                'openInterest': {'openInterest': '1000'}, 'candles': {i: bars(60) for i in ('5m', '15m', '1h', '4h', '1d')}}
    snapshot['indicators'] = indicators.compute(snapshot)
    return snapshot


class FakeLMStudio:
    """Answers with fixed sentences and keeps what it was sent."""
    def __init__(self, summary='- 추세는 위지만 거래량이 부족해 관망입니다.,', risk='펀딩이 높아 롱 쏠림이 보입니다.'):
        self.summary, self.risk, self.calls = summary, risk, []

    def __call__(self, path, payload=None):
        self.calls.append(payload)
        body = {h: {'summary': self.summary, 'risk': self.risk} for h in llm.HORIZONS}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(body, ensure_ascii=False)}}]}


MACRO = {'facts': {'macro.vix': 19.03, 'macro.us10yPct': 5.13}, 'dates': {'vix': '2026-05-27'}, 'warnings': ['거시 주의'],
         'calendar': [{'time': '2026-05-28T12:30:00+00:00', 'event': 'CPI', 'status': '예정', 'consensus': '', 'previous': '', 'actual': ''}],
         'news': [{'id': 'news-1', 'date': '2026-05-27', 'title': 'Bitcoin ETF inflows', 'excerpt': 'x', 'publisher': 'Zacks',
                   'url': 'https://www.nasdaq.com/a'}]}


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = make_snapshot()
        patcher = patch.object(llm, 'RETRY_DELAY', 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def analyze(self, fake, config=CONFIG):
        with patch.object(llm, 'request', side_effect=fake):
            return llm.analyze(self.snapshot, config)

    def test_code_makes_the_call_and_the_model_only_explains(self):
        fake = FakeLMStudio()
        report = self.analyze(fake)
        self.assertEqual(len(fake.calls), 1)
        sent = json.loads(fake.calls[0]['messages'][1]['content'])
        facts = report['facts']
        for h, data in report['horizons'].items():
            self.assertEqual(data['verdict'], signals.decide(facts, h))
            self.assertEqual(sent['horizons'][h]['verdict'], data['verdict'])
            self.assertEqual(sent['horizons'][h]['checks'], signals.checks(facts, h))
            self.assertEqual(data['summary'], '추세는 위지만 거래량이 부족해 관망입니다.')  # bullet and trailing comma removed
            self.assertEqual(data['disagreement'], '펀딩이 높아 롱 쏠림이 보입니다.')
            self.assertEqual(data['explainer'], {'model': 'model-a'})
            self.assertEqual([a['id'] for a in data['agents']], ['rule'])
            self.assertEqual(data['agents'][0]['evidence'], signals.checks(facts, h))
        self.assertTrue(all(k.split('.')[0] in ('market', '5m', '15m', '1h') for k in sent['horizons']['intraday']['facts']))
        self.assertTrue(all(k.split('.')[0] in ('market', '4h', '1d') for k in sent['horizons']['swing']['facts']))
        self.assertNotIn('verdict', json.dumps(llm.SCHEMA))  # the model has no field to make a call in
        self.assertEqual(fake.calls[0]['reasoning_effort'], 'none')
        self.assertEqual((report['promptVersion'], report['origin']), (signals.VERSION, 'local-llm'))
        self.assertIn('시각 주의', report['warnings'])

    def test_without_a_config_the_code_writes_the_report(self):
        with patch.object(llm, 'request') as call:
            report = llm.analyze(self.snapshot)
        call.assert_not_called()
        swing = report['horizons']['swing']
        self.assertIsNone(swing['explainer'])
        self.assertIn('조건', swing['summary'])
        self.assertIn('해설 모델을 실행하지 않아', swing['disagreement'])

    def test_failed_explanation_keeps_the_calls(self):
        seen = []
        with patch.object(llm.progress, 'role', side_effect=lambda r, s, reason=None: seen.append((r, s))):
            report = self.analyze(llm.AnalysisError('down'))
        self.assertEqual(seen, [('explainer', 'running'), ('explainer', 'failed')])
        self.assertEqual(report['horizons']['swing']['verdict'], signals.decide(report['facts'], 'swing'))
        self.assertIsNone(report['horizons']['swing']['explainer'])
        self.assertTrue(any('판정은 코드가 낸 그대로' in w for w in report['warnings']))

    def test_invalid_and_truncated_outputs_fall_back(self):
        def truncated(path, payload=None):
            return {'choices': [{'finish_reason': 'length'}]}

        def not_json(path, payload=None):
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': 'not json'}}]}
        for fake in (truncated, not_json):
            with self.subTest(fake=fake.__name__):
                report = self.analyze(fake)
                self.assertIsNone(report['horizons']['intraday']['explainer'])
                self.assertTrue(any('model-a' in w for w in report['warnings']))

    def test_retries_once(self):
        fake = FakeLMStudio()
        with patch.object(llm, 'request', side_effect=[llm.AnalysisError('busy'), fake('/chat/completions', {})]) as call:
            llm.chat('model-a', {}, 0.1)
        self.assertEqual(call.call_count, 2)

    def test_numbers_in_text_flagged(self):
        report = self.analyze(FakeLMStudio(risk='펀딩이 0.05%로 높습니다.'))
        self.assertTrue(any('검증하지 않은 숫자' in w for w in report['warnings']))
        self.assertFalse(llm.numbers_in('4h RSI14와 20봉 고점을 봅니다.'))

    def test_macro_and_news_go_to_the_model_and_sources(self):
        self.snapshot['macro'] = MACRO
        fake = FakeLMStudio()
        report = self.analyze(fake)
        sent = json.loads(fake.calls[0]['messages'][1]['content'])
        self.assertEqual(sent['macro']['calendar'][0]['event'], 'CPI')
        self.assertEqual(sent['macro']['news'][0]['title'], 'Bitcoin ETF inflows')
        self.assertNotIn('url', sent['macro']['news'][0])
        sources = report['horizons']['swing']['agents'][0]['sources']
        self.assertIn('2026-05-27', sources[1]['title'])
        self.assertEqual(sources[2]['url'], 'https://www.nasdaq.com/a')
        self.assertIn('거시 주의', report['warnings'])

    def test_missing_macro_is_a_warning(self):
        self.snapshot['macro'] = {'error': 'OpenBB 가상환경(.venv)이 없습니다.'}
        fake = FakeLMStudio()
        report = self.analyze(fake)
        self.assertNotIn('macro', json.loads(fake.calls[0]['messages'][1]['content']))
        self.assertTrue(any('뉴스·거시 자료: OpenBB 가상환경' in w for w in report['warnings']))

    def test_tidy_keeps_minus_signs(self):
        self.assertEqual(llm.tidy({'a': ['- 문장입니다.,', '-0.5% 하락', '* 강조 문장*']}),
                         {'a': ['문장입니다.', '-0.5% 하락', '강조 문장']})


class ConfigTests(unittest.TestCase):
    def test_config_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'models.json'
            for bad in ('not json', {'roles': {'technical': 'a'}}, {'model': ' '}, {'model': 'a', 'temperature': 3}):
                path.write_text(bad if isinstance(bad, str) else json.dumps(bad), encoding='utf-8')
                with self.subTest(bad=bad), self.assertRaises(llm.AnalysisError):
                    llm.load_config(path)
            path.write_text(json.dumps({'model': ' model-a '}), encoding='utf-8')
            self.assertEqual(llm.load_config(path), CONFIG)
        self.assertTrue(llm.load_config()['model'])

    def test_status_reports_the_model(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'models.json'
            path.write_text(json.dumps(CONFIG), encoding='utf-8')
            with patch.object(llm, 'CONFIG', path), patch.object(llm, 'request', return_value={'data': [{'id': 'model-b'}]}):
                state = llm.status()
        self.assertFalse(state['ready'])
        self.assertEqual(state['roles'], [{'role': 'explainer', 'name': '해설 모델', 'model': 'model-a', 'available': False}])

    def test_connection_failure(self):
        with patch.object(llm, 'build_opener') as opener:
            opener.return_value.open.side_effect = OSError('offline')
            with self.assertRaises(llm.AnalysisError):
                llm.status()


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'timings.json'
        self.progress = llm.Progress(self.path)

    def test_role_lifecycle_and_timing_memory(self):
        self.progress.begin('BTCUSDT', {'explainer': 'model-a'})
        state = self.progress.snapshot()
        self.assertEqual((state['state'], state['stage'], state['symbol']), ('running', 'collecting', 'BTCUSDT'))
        self.assertEqual(state['roles'][0]['expectedMs'], llm.DEFAULT_EXPECTED_MS)
        self.progress.role('explainer', 'running')
        state = self.progress.snapshot()
        self.assertEqual(state['stage'], 'analyzing')
        self.assertIn('elapsedMs', state['roles'][0])
        self.assertNotIn('started', state['roles'][0])
        self.progress.role('explainer', 'done')
        self.assertIn('durationMs', self.progress.snapshot()['roles'][0])
        self.assertIn('model-a', json.loads(self.path.read_text(encoding='utf-8')))
        self.progress.begin('ETHUSDT', {'explainer': 'model-a'})
        self.assertLess(self.progress.snapshot()['roles'][0]['expectedMs'], 60000)
        self.progress.role('explainer', 'running')
        self.progress.finish(error='해설 모델(model-a): LM Studio 오류')
        state = self.progress.snapshot()
        self.assertEqual((state['state'], state['roles'][0]['status'], state['roles'][0]['reason']), ('failed', 'failed', 'LM Studio 오류'))

    def test_finish_keeps_report_and_ignores_when_idle(self):
        self.progress.finish(error='무시')
        self.assertEqual(self.progress.snapshot(), {'state': 'idle'})
        self.progress.begin('BTCUSDT', {'explainer': 'model-a'})
        self.progress.finish(report={'id': 'r1'})
        self.progress.finish(error='늦은 오류')
        state = self.progress.snapshot()
        self.assertEqual((state['state'], state['reportId']), ('done', 'r1'))
        self.assertEqual(state['elapsedMs'], state['durationMs'])
        self.assertEqual(self.progress.last_report, {'id': 'r1'})

    def test_bad_timing_file_falls_back(self):
        self.path.write_text('{"model-a": -5, "model-b": "x"}', encoding='utf-8')
        self.progress.begin('BTCUSDT', {'explainer': 'model-a'})
        self.assertEqual(self.progress.snapshot()['roles'][0]['expectedMs'], llm.DEFAULT_EXPECTED_MS)

    def test_describe_macro(self):
        lines = indicators.describe({'macro.us10yChange5dBp': -6.0, 'macro.vix': 26.0, 'macro.curve2s10sBp': -5.0},
                                    ['macro.us10yChange5dBp', 'macro.vix', 'macro.curve2s10sBp'])
        self.assertEqual(lines, ['미국 10년 금리 5일 변화 -6.0bp: 하락', 'VIX 26.0: 변동성 경계 높음(25 이상)',
                                 '미국 10년-2년 금리차 -5.0bp: 역전(2년이 높음)'])


if __name__ == '__main__':
    unittest.main()
