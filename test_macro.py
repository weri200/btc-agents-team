import json
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import macro

AS_OF = int(datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)


def raw():
    days = [f'2026-10-{d:02d}' for d in (14, 15, 16, 17, 19, 20, 21, 22)]
    return {
        'series': {'vix': [[d, 15 + i] for i, d in enumerate(days)] + [['2026-10-24', 99]],
                   'spx': [[d, 1000 + 10 * i] for i, d in enumerate(days)],
                   'ndx': [[d, 2000] for d in days]},
        'treasury': [[d, 0.048, 0.05 + i / 10000] for i, d in enumerate(days)],
        'calendar': [
            {'time': '2026-10-23T12:30:00+00:00', 'event': 'Initial Jobless Claims', 'consensus': '200K', 'previous': '200K', 'actual': '210K'},
            {'time': '2026-10-23T11:30:00+00:00', 'event': 'CPI (YoY)', 'consensus': '3.0%', 'previous': '3.1%', 'actual': '2.9%'},
            {'time': '2026-10-23T14:30:00+00:00', 'event': 'Crude Oil Inventories', 'consensus': '', 'previous': '', 'actual': ''},
            {'time': '2026-10-30T12:30:00+00:00', 'event': 'GDP', 'consensus': '', 'previous': '', 'actual': ''},
        ],
        'news': [{'date': '2026-10-22', 'title': 'Bitcoin ETF inflows', 'excerpt': ' many   spaces ', 'publisher': 'Zacks',
                  'url': 'https://www.nasdaq.com/a'},
                 {'date': '2026-10-24', 'title': 'Future article', 'excerpt': '', 'publisher': 'X', 'url': 'https://x'},
                 {'date': '2026-10-01', 'title': 'Old article', 'excerpt': '', 'publisher': 'X', 'url': 'https://y'},
                 {'date': '2026-10-21', 'title': 'Unsafe link', 'excerpt': '', 'publisher': 'X', 'url': 'javascript:alert(1)'}],
        'warnings': ['Nasdaq 뉴스 수집 실패: Timeout'],
    }


class BuildTests(unittest.TestCase):
    def test_facts_from_daily_series_without_future_rows(self):
        result = macro.build(raw(), AS_OF)
        facts = result['facts']
        self.assertEqual(facts['macro.vix'], 22)  # 2026-10-24 row is after the analysis date.
        self.assertEqual(facts['macro.vixChange1dPct'], round((22 / 21 - 1) * 100, 3))
        self.assertEqual(facts['macro.spxChange5dPct'], round((1070 / 1020 - 1) * 100, 3))
        self.assertEqual(facts['macro.us10yPct'], 5.07)
        self.assertEqual(facts['macro.us10yChange5dBp'], 5.0)
        self.assertEqual(facts['macro.curve2s10sBp'], 27.0)
        self.assertEqual(result['dates']['vix'], '2026-10-22')
        self.assertIn('Nasdaq 뉴스 수집 실패: Timeout', result['warnings'])

    def test_calendar_keeps_major_events_and_hides_future_results(self):
        calendar = macro.build(raw(), AS_OF)['calendar']
        self.assertEqual([c['event'] for c in calendar], ['CPI (YoY)', 'Initial Jobless Claims'])
        released, upcoming = calendar
        self.assertEqual((released['status'], released['actual']), ('발표됨', '2.9%'))
        self.assertEqual((upcoming['status'], upcoming['actual']), ('예정', ''))

    def test_news_window_ids_and_links(self):
        news = macro.build(raw(), AS_OF)['news']
        self.assertEqual([n['title'] for n in news], ['Bitcoin ETF inflows', 'Unsafe link'])
        self.assertEqual([n['id'] for n in news], ['news-1', 'news-2'])
        self.assertEqual(news[0]['excerpt'], 'many spaces')
        self.assertIsNone(news[1]['url'])

    def test_stale_series_warns(self):
        data = raw()
        data['series'] = {'vix': [['2026-10-10', 20], ['2026-10-12', 21]]}
        data['treasury'] = []
        self.assertTrue(any('vix' in w and '오래' in w for w in macro.build(data, AS_OF)['warnings']))

    def test_rejects_bad_payloads(self):
        for mutate in (lambda d: d['series'].__setitem__('vix', 'x'), lambda d: d['news'][0].__setitem__('date', '22/10'),
                       lambda d: d['calendar'][0].__setitem__('time', '2026-10-23T12:30:00'),
                       lambda d: d['treasury'].append(['2026-10-22', 'NaN?', 1])):
            data = raw()
            mutate(data)
            with self.subTest(mutate=mutate), self.assertRaises(macro.MacroUnavailable):
                macro.build(data, AS_OF)
        with self.assertRaises(macro.MacroUnavailable):
            macro.build([], AS_OF)
        with self.assertRaises(macro.MacroUnavailable):
            macro.build({'series': {}, 'treasury': [], 'calendar': [], 'news': []}, AS_OF)


class FetchTests(unittest.TestCase):
    def test_missing_venv(self):
        with patch.dict('os.environ', {'OPENBB_PYTHON': str(Path('does-not-exist') / 'python.exe')}):
            with self.assertRaisesRegex(macro.MacroUnavailable, '가상환경'):
                macro.fetch('BTCUSDT', AS_OF)

    def test_reads_last_json_line_after_build_messages(self):
        done = subprocess.CompletedProcess([], 0, ('Building...\n' + json.dumps(raw())).encode())
        with patch.object(macro, 'python_path', return_value=Path(__file__)), patch.object(macro.subprocess, 'run', return_value=done) as run:
            self.assertEqual(macro.fetch('BTCUSDT', AS_OF)['warnings'], raw()['warnings'])
        self.assertEqual(run.call_args.args[0][-2:], ['BTCUSDT', str(AS_OF)])

    def test_failure_message_keeps_last_error_line(self):
        failed = subprocess.CompletedProcess([], 1, b'', b'Traceback ...\nModuleNotFoundError: No module named openbb\n')
        with patch.object(macro, 'python_path', return_value=Path(__file__)), patch.object(macro.subprocess, 'run', return_value=failed):
            with self.assertRaisesRegex(macro.MacroUnavailable, r'오류로 끝났습니다\. \(ModuleNotFoundError: No module named openbb\)$'):
                macro.fetch('BTCUSDT', AS_OF)

    def test_failures_become_unavailable(self):
        cases = [subprocess.CompletedProcess([], 1, b''), subprocess.CompletedProcess([], 0, b'not json'),
                 subprocess.TimeoutExpired('x', 1)]
        for case in cases:
            effect = {'side_effect': case} if isinstance(case, Exception) else {'return_value': case}
            with self.subTest(case=case), patch.object(macro, 'python_path', return_value=Path(__file__)), \
                    patch.object(macro.subprocess, 'run', **effect), self.assertRaises(macro.MacroUnavailable):
                macro.fetch('BTCUSDT', AS_OF)


if __name__ == '__main__':
    unittest.main()
