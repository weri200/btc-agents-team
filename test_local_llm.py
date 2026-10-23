import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import indicators
import local_llm as llm

CONFIG = {'roles': {'technical': 'model-a', 'derivatives': 'model-b', 'critic': 'model-c', 'judge': 'model-d'},
          'temperature': 0.1}


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
    """Answers each role from the facts it was sent; `wrong` roles cite a bad value."""
    def __init__(self, verdicts=None, judge='long', wrong=()):
        self.verdicts = verdicts or {'technical': 'long', 'derivatives': 'long', 'macro': 'wait', 'critic': 'wait'}
        self.judge, self.wrong, self.calls, self.payloads = judge, set(wrong), [], []

    def __call__(self, path, payload=None):
        role = payload['response_format']['json_schema']['name']
        self.payloads.append(payload)
        self.calls.append((role, payload['model'], json.loads(payload['messages'][1]['content'])))
        sent = self.calls[-1][2]
        if role == 'judge':
            body = {h: {'verdict': self.judge, 'summary': '종합 요약 문장입니다.', 'agreement': '공통 의견 문장입니다.',
                        'disagreement': '남은 이견 문장입니다.', 'condition': '재검토 조건 문장입니다.'}
                    for h in llm.HORIZONS}
        else:
            body = {}
            schema = payload['response_format']['json_schema'].get('schema') or {}
            for h in llm.HORIZONS:
                key = ('macro.vix' if role == 'macro' else
                       f"{llm.HORIZONS[h][2][0]}.{'change1Pct' if role == 'derivatives' else 'rsi14'}")
                value = sent['facts'][h][key] + (30 if role in self.wrong else 0.004)
                body[h] = {'verdict': self.verdicts[role], 'summary': '- RSI14 기준 과열 구간입니다.,',
                           'evidence': [{'ref': key, 'value': value, 'claim': '모멘텀이 강합니다.'}],
                           'counter': '반락할 가능성이 있습니다.', 'invalidation': '추세선을 이탈하면 철회합니다.'}
                if 'news' in schema.get('properties', {}).get(h, {}).get('properties', {}):
                    body[h]['news'] = [n['id'] for n in sent['news'][:1]] * 2  # Duplicate ids are collapsed.
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(body, ensure_ascii=False)}}]}


MACRO = {'facts': {'macro.vix': 19.03, 'macro.us10yPct': 5.13, 'macro.spxChange1dPct': -1.2},
         'dates': {'vix': '2026-05-27'}, 'warnings': ['거시 주의'],
         'calendar': [{'time': '2026-05-28T12:30:00+00:00', 'event': 'CPI', 'status': '예정', 'consensus': '', 'previous': '', 'actual': ''}],
         'news': [{'id': 'news-1', 'date': '2026-05-27', 'title': 'Bitcoin ETF inflows', 'excerpt': 'x', 'publisher': 'Zacks',
                   'url': 'https://www.nasdaq.com/a'}]}
MACRO_CONFIG = {'roles': {**CONFIG['roles'], 'macro': 'model-e'}, 'temperature': 0.1}


class LocalTeamTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = make_snapshot()
        patcher = patch.object(llm, 'RETRY_DELAY', 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_team(self, fake):
        with patch.object(llm, 'request', side_effect=fake):
            return llm.analyze(self.snapshot, CONFIG)

    def test_each_role_uses_its_model_and_verified_report(self):
        fake = FakeLMStudio()
        report = self.run_team(fake)
        self.assertEqual([(r, m) for r, m, _ in fake.calls],
                         [('technical', 'model-a'), ('derivatives', 'model-b'), ('critic', 'model-c'), ('judge', 'model-d')])
        self.assertIn('team', fake.calls[2][2])
        self.assertTrue(all(p['reasoning_effort'] == 'none' for p in fake.payloads))
        self.assertNotIn('facts', fake.calls[3][2])
        for h in report['horizons'].values():
            self.assertEqual([a['id'] for a in h['agents']], ['technical', 'derivatives', 'macro', 'critic'])
            self.assertEqual(h['verdict'], 'long')
            self.assertEqual(h['agents'][0]['model'], 'model-a')
            self.assertEqual(h['agents'][0]['verification']['matched'], 1)
            self.assertIn('일치', h['agents'][0]['evidence'][0])
        self.assertEqual(report['promptVersion'], llm.PROMPT_VERSION)
        self.assertIn('시각 주의', report['warnings'])

    def test_horizon_and_role_facts_are_scoped(self):
        self.snapshot['derivatives'] = {'funding': [{'time': 0, 'fundingRate': '0.0001'}], 'warnings': ['이력 주의'],
                                        'periods': {p: {'openInterest': [{'time': 0, 'sumOpenInterest': '5', 'sumOpenInterestValue': '9'}],
                                                        'globalLongShort': [{'time': 0, 'longShortRatio': '1.2'}],
                                                        'topLongShort': [{'time': 0, 'longShortRatio': '1.1'}],
                                                        'taker': [{'time': 0, 'buySellRatio': '0.9'}]} for p in ('1h', '4h')}}
        fake = FakeLMStudio()
        report = self.run_team(fake)
        sent = {role: payload['facts'] for role, _, payload in fake.calls if role != 'judge'}
        self.assertTrue(all(k.split('.')[0] in ('market', '5m', '15m', '1h') for k in sent['technical']['intraday']))
        self.assertTrue(all(k.split('.')[0] in ('market', '4h', '1d') for k in sent['technical']['swing']))
        self.assertNotIn('1h.longShortRatio', sent['technical']['intraday'])
        self.assertIn('1h.rsi14', sent['technical']['intraday'])
        self.assertIn('1h.longShortRatio', sent['derivatives']['intraday'])
        self.assertIn('4h.oiValueUsd', sent['derivatives']['swing'])
        self.assertNotIn('1h.rsi14', sent['derivatives']['intraday'])
        self.assertIn('1h.rsi14', sent['critic']['intraday'])
        self.assertIn('1h.takerBuySellRatio', sent['critic']['intraday'])
        self.assertIn('이력 주의', report['warnings'])

    def test_retries_once_then_fails(self):
        with patch.object(llm, 'request', side_effect=[llm.AnalysisError('busy'), FakeLMStudio()('/chat/completions', {
                'model': 'model-a', 'messages': [{}, {'content': json.dumps({'facts': {h: {f"{llm.HORIZONS[h][2][0]}.rsi14": 1}
                                                                                   for h in llm.HORIZONS}})}],
                'response_format': {'json_schema': {'name': 'technical'}}})]) as call:
            schema = llm.analyst_schema({h: [f"{llm.HORIZONS[h][2][0]}.rsi14"] for h in llm.HORIZONS})
            llm.chat('technical', 'model-a', {}, schema, 0.1)
        self.assertEqual(call.call_count, 2)
        with patch.object(llm, 'request', side_effect=llm.AnalysisError('down')) as call, self.assertRaises(llm.AnalysisError):
            llm.chat('technical', 'model-a', {}, schema, 0.1)
        self.assertEqual(call.call_count, 2)

    def test_wrong_citation_forces_wait(self):
        seen = []
        with patch.object(llm.progress, 'role', side_effect=lambda r, s, reason=None: seen.append((r, s, reason))):
            report = self.run_team(FakeLMStudio(wrong={'technical'}))
        self.assertIn(('technical', 'done', '단타·스윙 인용 수치가 서버 계산값과 달라 관망으로 바꿨습니다.'), seen)
        self.assertIn(('critic', 'done', None), seen)  # No reason when every citation matches.
        agent = report['horizons']['intraday']['agents'][0]
        self.assertEqual(agent['verdict'], 'wait')
        self.assertEqual(agent['verification']['originalVerdict'], 'long')
        self.assertIn('불일치', agent['evidence'][0])
        self.assertTrue(any('불일치로 관망' in w for w in report['warnings']))

    def test_judge_cannot_pick_unsupported_verdict(self):
        report = self.run_team(FakeLMStudio(judge='short'))
        self.assertEqual(report['horizons']['swing']['verdict'], 'wait')
        self.assertEqual(report['horizons']['swing']['judge']['originalVerdict'], 'short')

    def test_numbers_in_text_flagged(self):
        report = self.run_team(FakeLMStudio())
        self.assertFalse(report['horizons']['intraday']['agents'][0]['verification']['numbersInText'])
        check = llm.verify({'summary': '가격이 67000을 넘었습니다.', 'counter': 'x', 'invalidation': 'y', 'evidence': []}, {})
        self.assertTrue(check['numbersInText'])

    def test_matches_tolerance(self):
        self.assertTrue(llm.matches(28, 28.43))
        self.assertTrue(llm.matches(28.4, 28.43))
        self.assertTrue(llm.matches(67000, 67123.4))
        self.assertFalse(llm.matches(70000, 67123.4))
        self.assertFalse(llm.matches(0, 0.0012))
        self.assertTrue(llm.matches(0.0012, 0.00123))
        self.assertFalse(llm.matches(35, 28.43))

    def test_invalid_and_truncated_outputs(self):
        def truncated(path, payload=None):
            return {'choices': [{'finish_reason': 'length'}]}
        def not_json(path, payload=None):
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': 'not json'}}]}
        def bad_ref(path, payload=None):
            response = FakeLMStudio()(path, payload)
            body = json.loads(response['choices'][0]['message']['content'])
            body['intraday']['evidence'][0]['ref'] = '1d.rsi14'
            response['choices'][0]['message']['content'] = json.dumps(body)
            return response
        for fake in (truncated, not_json, bad_ref):
            with self.subTest(fake=fake.__name__), self.assertRaises(llm.AnalysisError) as error:
                with patch.object(llm, 'request', side_effect=fake):
                    llm.analyze(self.snapshot, CONFIG)
            self.assertIn('model-a', str(error.exception))

    def test_status_reports_missing_and_duplicate_models(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'models.json'
            path.write_text(json.dumps({'roles': {**CONFIG['roles'], 'judge': 'model-a'}}), encoding='utf-8')
            with patch.object(llm, 'CONFIG', path), \
                    patch.object(llm, 'request', return_value={'data': [{'id': 'model-a'}, {'id': 'model-b'}]}):
                state = llm.status()
        self.assertFalse(state['ready'])
        self.assertEqual([r['available'] for r in state['roles']], [True, True, False, True])
        self.assertTrue(state['warnings'])

    def test_config_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'models.json'
            for bad in ('not json', {'roles': {'technical': 'a'}}, {'roles': CONFIG['roles'], 'temperature': 3}):
                path.write_text(bad if isinstance(bad, str) else json.dumps(bad), encoding='utf-8')
                with self.subTest(bad=bad), self.assertRaises(llm.AnalysisError):
                    llm.load_config(path)
            path.write_text(json.dumps(CONFIG), encoding='utf-8')
            self.assertEqual(llm.load_config(path)['roles']['judge'], 'model-d')
        self.assertEqual(set(llm.load_config()['roles']), set(llm.ROLES))

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
        self.progress.begin('BTCUSDT', CONFIG['roles'])
        state = self.progress.snapshot()
        self.assertEqual((state['state'], state['stage'], state['symbol']), ('running', 'collecting', 'BTCUSDT'))
        self.assertEqual([r['status'] for r in state['roles']], ['pending'] * 4)
        self.assertEqual(state['roles'][0]['expectedMs'], llm.DEFAULT_EXPECTED_MS)
        self.progress.role('technical', 'running')
        state = self.progress.snapshot()
        self.assertEqual(state['stage'], 'analyzing')
        self.assertIn('elapsedMs', state['roles'][0])
        self.assertNotIn('started', state['roles'][0])
        self.progress.role('technical', 'done')
        self.assertIn('durationMs', self.progress.snapshot()['roles'][0])
        self.progress.role('derivatives', 'running')
        self.progress.finish(error='파생상품 분석가(model-b): LM Studio 오류')
        state = self.progress.snapshot()
        self.assertEqual((state['state'], state['error']), ('failed', '파생상품 분석가(model-b): LM Studio 오류'))
        self.assertEqual([r['status'] for r in state['roles']][:2], ['done', 'failed'])
        self.assertEqual([r.get('reason') for r in state['roles']],
                         [None, 'LM Studio 오류', *['앞 단계에서 분석이 중단되어 실행하지 않았습니다.'] * 2])
        self.assertIn('model-a', json.loads(self.path.read_text(encoding='utf-8')))
        self.progress.begin('ETHUSDT', CONFIG['roles'])
        self.assertLess(self.progress.snapshot()['roles'][0]['expectedMs'], 60000)

    def test_finish_keeps_report_and_ignores_when_idle(self):
        self.progress.finish(error='무시')
        self.assertEqual(self.progress.snapshot(), {'state': 'idle'})
        self.progress.begin('BTCUSDT', CONFIG['roles'])
        self.progress.finish(report={'id': 'r1'})
        self.progress.finish(error='늦은 오류')
        state = self.progress.snapshot()
        self.assertEqual((state['state'], state['reportId']), ('done', 'r1'))
        self.assertEqual(state['elapsedMs'], state['durationMs'])
        self.assertEqual(self.progress.last_report, {'id': 'r1'})

    def test_bad_timing_file_falls_back(self):
        self.path.write_text('{"model-a": -5, "model-b": "x"}', encoding='utf-8')
        self.progress.begin('BTCUSDT', CONFIG['roles'])
        self.assertEqual({r['expectedMs'] for r in self.progress.snapshot()['roles']}, {llm.DEFAULT_EXPECTED_MS})

    def test_analyze_reports_each_role(self):
        seen = []
        with patch.object(llm.progress, 'role', side_effect=lambda r, s, reason=None: seen.append((r, s))),                 patch.object(llm, 'request', side_effect=FakeLMStudio()):
            llm.analyze(make_snapshot(), CONFIG)
        self.assertEqual(seen, [(r, s) for r in ('technical', 'derivatives', 'critic', 'judge') for s in ('running', 'done')])



class MacroRoleTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = make_snapshot()

    def run_team(self, fake, macro=MACRO, config=MACRO_CONFIG):
        self.snapshot['macro'] = macro
        with patch.object(llm, 'request', side_effect=fake):
            return llm.analyze(self.snapshot, config)

    def test_macro_runs_between_derivatives_and_critic(self):
        fake = FakeLMStudio()
        report = self.run_team(fake)
        self.assertEqual([r for r, _, _ in fake.calls], ['technical', 'derivatives', 'macro', 'critic', 'judge'])
        macro_payload = fake.calls[2][2]
        self.assertTrue(all(k.startswith('macro.') for h in llm.HORIZONS for k in macro_payload['facts'][h]))
        self.assertEqual(macro_payload['news'][0]['id'], 'news-1')
        self.assertNotIn('url', macro_payload['news'][0])
        self.assertEqual(macro_payload['calendar'][0]['event'], 'CPI')
        self.assertNotIn('macro.vix', fake.calls[0][2]['facts']['intraday'])
        self.assertIn('macro.vix', fake.calls[3][2]['facts']['intraday'])
        self.assertEqual([t['role'] for t in fake.calls[3][2]['team']['swing']], ['기술 분석가', '파생상품 분석가', '뉴스·거시 분석가'])
        self.assertEqual(len(fake.calls[4][2]['team']['swing']), 4)
        self.assertNotIn('missing', fake.calls[4][2])
        agent = report['horizons']['intraday']['agents'][2]
        self.assertEqual((agent['id'], agent['model'], agent['verification']['matched']), ('macro', 'model-e', 1))
        self.assertEqual(agent['news'], ['news-1'])
        self.assertEqual(agent['sources'][1]['url'], 'https://www.nasdaq.com/a')
        self.assertEqual(agent['sources'][1]['asOf'], '2026-05-27T00:00:00+00:00')
        self.assertIn('2026-05-27', agent['sources'][0]['title'])
        self.assertEqual(agent['summary'], 'RSI14 기준 과열 구간입니다.')  # Bullet and trailing comma removed.
        self.assertIn('거시 주의', report['warnings'])
        self.assertEqual(report['facts']['macro.vix'], 19.03)

    def test_macro_without_news_sends_empty_enum(self):
        fake = FakeLMStudio()
        self.run_team(fake, {**MACRO, 'news': []})
        schema = fake.payloads[2]['response_format']['json_schema']['schema']['properties']['intraday']['properties']['news']
        self.assertEqual((schema['maxItems'], schema['items']['enum']), (0, ['none']))

    def test_unavailable_macro_keeps_placeholder(self):
        fake = FakeLMStudio()
        seen = []
        with patch.object(llm.progress, 'role', side_effect=lambda r, s, reason=None: seen.append((r, s, reason))):
            report = self.run_team(fake, {'error': 'OpenBB 가상환경(.venv)이 없습니다.'})
        self.assertEqual([r for r, _, _ in fake.calls], ['technical', 'derivatives', 'critic', 'judge'])
        self.assertIn(('macro', 'skipped', 'OpenBB 가상환경(.venv)이 없습니다.'), seen)
        agent = report['horizons']['swing']['agents'][2]
        self.assertEqual((agent['id'], agent['verdict'], agent['model']), ('macro', 'wait', '미실행 · 자료 부족'))
        self.assertIn('가상환경', agent['evidence'][0])
        self.assertIn('missing', fake.calls[3][2])
        self.assertTrue(any('뉴스·거시 분석가: OpenBB 가상환경' in w for w in report['warnings']))

    def test_macro_role_is_optional_in_config(self):
        fake = FakeLMStudio()
        report = self.run_team(fake, config=CONFIG)
        self.assertNotIn('macro', [r for r, _, _ in fake.calls])
        self.assertIn('models.json', report['horizons']['intraday']['agents'][2]['evidence'][0])
        self.assertNotIn('macro.vix', report['facts'])

    def test_wrong_macro_citation_forces_wait(self):
        report = self.run_team(FakeLMStudio(verdicts={'technical': 'long', 'derivatives': 'long', 'macro': 'short', 'critic': 'wait'},
                                            wrong={'macro'}))
        agent = report['horizons']['intraday']['agents'][2]
        self.assertEqual((agent['verdict'], agent['verification']['originalVerdict']), ('wait', 'short'))

    def test_config_order_and_describe(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'models.json'
            roles = {'judge': 'd', 'critic': 'c', 'macro': 'e', 'derivatives': 'b', 'technical': 'a'}
            path.write_text(json.dumps({'roles': roles}), encoding='utf-8')
            self.assertEqual(list(llm.load_config(path)['roles']), ['technical', 'derivatives', 'macro', 'critic', 'judge'])
            path.write_text(json.dumps({'roles': {**roles, 'extra': 'x'}}), encoding='utf-8')
            with self.assertRaises(llm.AnalysisError):
                llm.load_config(path)
        lines = indicators.describe({'macro.us10yChange5dBp': -6.0, 'macro.vix': 26.0, 'macro.curve2s10sBp': -5.0},
                                    ['macro.us10yChange5dBp', 'macro.vix', 'macro.curve2s10sBp'])
        self.assertEqual(lines, ['미국 10년 금리 5일 변화 -6.0bp: 하락', 'VIX 26.0: 변동성 경계 높음(25 이상)',
                                 '미국 10년-2년 금리차 -5.0bp: 역전(2년이 높음)'])

    def test_tidy_keeps_minus_signs(self):
        self.assertEqual(llm.tidy({'a': ['- 문장입니다.,', '-0.5% 하락', '* 강조 문장*']}),
                         {'a': ['문장입니다.', '-0.5% 하락', '강조 문장']})


if __name__ == '__main__':
    unittest.main()
