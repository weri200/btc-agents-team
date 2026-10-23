import copy
import json
import unittest
from unittest.mock import patch

import local_llm as llm


def response_value(schema):
    if schema['type'] == 'object':
        return {k: response_value(v) for k, v in schema['properties'].items()}
    return schema.get('enum', ['확인 가능한 근거가 부족합니다.'])[0]


class LocalModelTests(unittest.TestCase):
    def setUp(self):
        self.output = response_value(llm.SCHEMA)
        self.snapshot = {'symbol': 'BTCUSDT', 'serverTime': 1780000000000, 'collectedAt': '2026-05-28T00:00:00Z',
                         'ticker': {}, 'mark': {}, 'openInterest': {}, 'warnings': ['시각 주의'],
                         'candles': {'5m': [dict(closeTime=i, open='1', high='2', low='1', close='2', volume='3') for i in range(40)]}}

    def test_report_and_bounded_input(self):
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.output)}}]}
        with patch.object(llm, 'request', return_value=response) as call:
            report = llm.analyze(self.snapshot)
        self.assertEqual(report['symbol'], 'BTCUSDT')
        self.assertEqual(report['origin'], 'local-llm')
        self.assertEqual(report['warnings'], ['시각 주의'])
        for h in report['horizons'].values():
            self.assertEqual(len(h['agents']), 4)
            self.assertEqual(h['agents'][2]['verdict'], 'wait')
            self.assertEqual(h['agents'][2]['model'], '미실행 · 자료 부족')
        sent = json.loads(call.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(len(sent['candles']['5m']), 30)
        self.assertEqual(sent['candles']['5m'][0][0], 10)

    def test_invalid_reports(self):
        for invalid in (None, [], {}, {'intraday': self.output['intraday']}):
            with self.assertRaises(llm.AnalysisError):
                llm.validate(invalid)
        for key, value in [('verdict', 'buy'), ('summary', ''), ('summary', 'x' * 701)]:
            invalid = copy.deepcopy(self.output)
            invalid['intraday'][key] = value
            with self.assertRaises(llm.AnalysisError):
                llm.validate(invalid)

    def test_truncated_and_invalid_json(self):
        for choice in ({'finish_reason': 'length'}, {'finish_reason': 'stop', 'message': {'content': 'not json'}}):
            with patch.object(llm, 'request', return_value={'choices': [choice]}):
                with self.assertRaises(llm.AnalysisError):
                    llm.analyze(self.snapshot)

    def test_connection_failure(self):
        with patch.object(llm, 'build_opener') as opener:
            opener.return_value.open.side_effect = OSError('offline')
            with self.assertRaises(llm.AnalysisError):
                llm.status()


if __name__ == '__main__':
    unittest.main()
