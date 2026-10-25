"""One LM Studio model explains the calls signals.py made: why the rule fired or not, and the risks it does not look at.
The model never decides; without it the report still carries every call, written out by the code."""
import json
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

import indicators
import signals
from market import iso

BASE = 'http://127.0.0.1:1234/v1'
CONFIG = Path(__file__).resolve().parent / 'models.json'
ROLES = {'explainer': '해설 모델'}
HORIZONS = {'intraday': ('단타', '5분 · 15분 · 1시간', ('5m', '15m', '1h')),
            'swing': ('스윙', '4시간 · 1일', ('4h', '1d'))}
RETRY_DELAY = 5
TIMINGS = Path(__file__).resolve().parent / 'data' / 'llm_timings.json'
DEFAULT_EXPECTED_MS = 60000
lock = threading.Lock()


class Progress:
    """Live state of the one running analysis, readable from any browser window."""
    def __init__(self, timings_path=None):
        self.timings_path = timings_path
        self.guard = threading.Lock()
        self.state = {'state': 'idle'}
        self.last_report = None

    def path(self):
        return Path(self.timings_path or TIMINGS)

    def timings(self):
        try:
            data = json.loads(self.path().read_text(encoding='utf-8'))
            return {k: v for k, v in data.items() if isinstance(k, str) and type(v) is int and 0 < v < 3_600_000}
        except (OSError, ValueError, AttributeError):
            return {}

    def begin(self, symbol, roles):
        expected = self.timings()
        with self.guard:
            self.state = {'state': 'running', 'stage': 'collecting', 'symbol': symbol, 'started': time.monotonic(),
                          'error': None, 'roles': [{'role': r, 'name': ROLES[r], 'model': m, 'status': 'pending',
                                                    'expectedMs': expected.get(m, DEFAULT_EXPECTED_MS)}
                                                   for r, m in roles.items()]}

    def role(self, role, status, reason=None):
        with self.guard:
            if self.state.get('state') != 'running':
                return
            self.state['stage'] = 'analyzing'
            for item in self.state['roles']:
                if item['role'] != role:
                    continue
                item['status'] = status
                if reason:
                    item['reason'] = reason
                if status == 'running':
                    item['started'] = time.monotonic()
                elif 'started' in item:
                    item['durationMs'] = round((time.monotonic() - item['started']) * 1000)
                if status == 'done':
                    self.remember(item['model'], item['durationMs'])

    def remember(self, model, duration):
        data = self.timings()
        data[model] = max(duration, 1)
        try:
            self.path().parent.mkdir(parents=True, exist_ok=True)
            self.path().write_text(json.dumps(data), encoding='utf-8')
        except OSError:
            pass  # Estimates only; a failed write must not fail the analysis.

    def finish(self, report=None, error=None):
        with self.guard:
            if self.state.get('state') != 'running':
                return
            self.state.update(state='failed' if error else 'done', error=error,
                              durationMs=round((time.monotonic() - self.state['started']) * 1000))
            for item in self.state['roles']:
                if error and item['status'] == 'running':
                    item['status'] = 'failed'
                    item['durationMs'] = round((time.monotonic() - item['started']) * 1000)
                    # The card already shows the name and model, so drop the "이름(모델)" prefix chat() adds.
                    item['reason'] = error.removeprefix(f"{item['name']}({item['model']})").lstrip(': ') or error
                elif error and item['status'] == 'pending':
                    item['reason'] = '앞 단계에서 분석이 중단되어 실행하지 않았습니다.'
            if report:
                self.last_report = report
                self.state['reportId'] = report['id']

    def snapshot(self):
        with self.guard:
            now = time.monotonic()
            result = {k: v for k, v in self.state.items() if k not in ('started', 'roles')}
            if 'started' in self.state:
                # A finished run reports its fixed duration, not time since it started.
                result['elapsedMs'] = (round((now - self.state['started']) * 1000) if self.state['state'] == 'running'
                                       else self.state['durationMs'])
            if 'roles' in self.state:
                result['roles'] = [{**{k: v for k, v in r.items() if k != 'started'},
                                    **({'elapsedMs': round((now - r['started']) * 1000)} if r['status'] == 'running' else {})}
                                   for r in self.state['roles']]
            return result


progress = Progress()


class AnalysisError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(path, payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = Request(BASE + path, data=data, headers={'Content-Type': 'application/json'})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=600 if data else 5) as response:
            raw = response.read(500_001)
        if len(raw) > 500_000:
            raise AnalysisError('모델 응답이 너무 큽니다.')
        return json.loads(raw)
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read(4000)).get('error')
            detail = detail.get('message') if isinstance(detail, dict) else detail
        except (OSError, ValueError, AttributeError):
            detail = None
        raise AnalysisError(f'LM Studio 요청 오류 {exc.code}: {str(detail or exc.reason)[:300]}') from exc
    except (URLError, OSError, ValueError) as exc:
        raise AnalysisError('LM Studio 연결 또는 응답 오류입니다. 포트 1234와 모델 로드 상태를 확인하세요.') from exc


def load_config(path=None):
    try:
        config = json.loads(Path(path or CONFIG).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise AnalysisError('models.json을 읽을 수 없습니다. 파일과 JSON 형식을 확인하세요.') from exc
    model = config.get('model') if isinstance(config, dict) else None
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise AnalysisError('models.json의 model에 해설 모델의 LM Studio ID 하나를 적으세요.')
    temperature = config.get('temperature', 0.1)
    if type(temperature) not in (int, float) or not 0 <= temperature <= 1:
        raise AnalysisError('models.json의 temperature는 0~1 사이 숫자여야 합니다.')
    return {'model': model.strip(), 'temperature': float(temperature)}


def status():
    config = load_config()
    result = request('/models')
    if not isinstance(result, dict) or not isinstance(result.get('data'), list) or any(not isinstance(m, dict) for m in result['data']):
        raise AnalysisError('LM Studio 모델 목록 형식이 올바르지 않습니다.')
    available = config['model'] in {m.get('id') for m in result['data']}
    return {'endpoint': BASE, 'ready': available, 'warnings': [],
            'roles': [{'role': 'explainer', 'name': ROLES['explainer'], 'model': config['model'], 'available': available}]}


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


TEXT = {'type': 'string', 'minLength': 6, 'maxLength': 300}
SCHEMA = obj({h: obj({'summary': TEXT, 'risk': TEXT}) for h in HORIZONS})


def validate(value, schema):
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            raise AnalysisError('모델 보고서의 필드가 올바르지 않습니다.')
        for key, child in schema['properties'].items():
            validate(value[key], child)
    elif (not isinstance(value, str) or len(value.strip()) < schema.get('minLength', 1)
          or len(value) > schema.get('maxLength', 300)):
        raise AnalysisError('모델 보고서의 문장 형식이 올바르지 않습니다.')
    return value


# Interval and indicator names contain digits but are not claims about values.
NAMES = re.compile(r'(?i)\b(?:5m|15m|1h|4h|1d)\b|\b(?:rsi|sma|atr|ema|ma)\s?\d+|\d+\s?(?:분|시간|일|기간|봉|개|년)\s?봉?'
                   r'|s&p\s?500|나스닥\s?100|2s10s')


def numbers_in(text):
    return bool(re.search(r'\d', NAMES.sub('', text)))


PROMPT = ('모든 문장은 반드시 한국어로 씁니다. 영어 문장을 쓰지 마세요. Binance USDⓈ-M 무기한 선물의 공개 시장 데이터입니다. '
          '주기(intraday 단타, swing 스윙)마다 verdict는 서버 코드가 rule과 checks로 이미 정한 판정입니다. 판정을 바꾸거나 다른 판정을 권하지 마세요. '
          'summary에는 checks를 바탕으로 이 판정이 나온 이유를 한 문장으로 씁니다. '
          'risk에는 규칙이 보지 않는 위험을 한두 문장으로 씁니다. facts와 signals의 펀딩비율, 미결제약정, 롱숏 비율, 테이커 비율, ATR 비율(변동성)과 '
          'macro의 금리·VIX·지수 변화, 경제 일정(calendar), 뉴스(news)에서 찾습니다. macro가 없으면 뉴스·거시 자료가 없다고 적습니다. '
          '자료에 없는 사실, 수치, 발표 결과를 만들지 마세요. 숫자는 쓰지 마세요. 조건 수치는 서버가 따로 보여줍니다. '
          '확률, 목표가, 수익 보장, 주문 지시를 하지 마세요. 문장은 120자 이내로 씁니다.')


def request_retry(path, payload):
    # LM Studio can briefly reject a request while JIT loads the model; one retry covers that.
    try:
        return request(path, payload)
    except AnalysisError:
        time.sleep(RETRY_DELAY)
        return request(path, payload)


def tidy(value):
    """Strip markdown bullets, emphasis, and dangling commas some models add to plain sentences."""
    if isinstance(value, dict):
        return {k: tidy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [tidy(v) for v in value]
    if isinstance(value, str):
        # Only a bullet followed by whitespace is removed, so a leading minus sign survives.
        cleaned = re.sub(r'^(?:\s*[-*•·]\s+)+', '', value.strip())
        if cleaned.count('*') == 1 and cleaned.endswith('*'):
            cleaned = cleaned[:-1]  # Closing half of an emphasis whose opener was taken as a bullet.
        elif len(cleaned) > 2 and cleaned.startswith('*') and cleaned.endswith('*'):
            cleaned = cleaned.strip('*').strip()
        cleaned = cleaned.rstrip(',').strip()
        return cleaned or value
    return value


def chat(model, payload, temperature):
    system = PROMPT + (' /no_think' if 'qwen' in model.lower() else '')
    label = f"{ROLES['explainer']}({model})"
    try:
        result = request_retry('/chat/completions', {
            # Reasoning models (e.g. Gemma 4) otherwise spend the whole token budget thinking before the JSON.
            'model': model, 'temperature': temperature, 'max_tokens': 4000, 'stream': False, 'reasoning_effort': 'none',
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'explainer', 'strict': True, 'schema': SCHEMA}},
        })
    except AnalysisError as exc:
        raise AnalysisError(f'{label} 호출 실패: {exc}') from exc
    try:
        choice = result['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise AnalysisError(f'{label} 응답이 끝나지 않아 해설을 사용하지 않았습니다.')
        return tidy(validate(json.loads(choice['message']['content']), SCHEMA))
    except AnalysisError as exc:
        raise AnalysisError(str(exc) if label in str(exc) else f'{label}: {exc}') from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AnalysisError(f'{label}: 유효한 JSON 해설을 반환하지 않았습니다.') from exc


LABELS = {'long': '롱', 'short': '숏', 'wait': '관망'}


def macro_sources_title(macro):
    dates = ', '.join(f'{k} {v}' for k, v in macro.get('dates', {}).items())
    return f'OpenBB 거시 지표 · 기준일 {dates}' if dates else 'OpenBB 거시 지표'


def analyze(snapshot, config=None):
    """The report for one snapshot: the code's call per horizon, explained by config's model when a config is given."""
    # Recompute so derivatives history fetched for this analysis is included.
    facts = indicators.flatten(indicators.compute(snapshot))
    macro = snapshot.get('macro') if isinstance(snapshot.get('macro'), dict) else {}
    stamp = iso(snapshot['serverTime'])
    warnings = (list(snapshot['warnings']) + list((snapshot.get('derivatives') or {}).get('warnings', []))
                + list(macro.get('warnings', [])))
    if macro.get('error'):
        warnings.append('뉴스·거시 자료: ' + macro['error'])
    calls = {h: {'verdict': signals.decide(facts, h), 'rule': signals.NAMES[h], 'checks': signals.checks(facts, h)} for h in HORIZONS}
    for h, call in calls.items():
        call['exit'] = signals.exit_plan(facts, h, call['verdict'])

    explained = {}
    if config:
        keys = {h: sorted(k for k in facts if k.startswith('market.') or k.split('.')[0] in frames)
                for h, (_, _, frames) in HORIZONS.items()}
        payload = {'symbol': snapshot['symbol'], 'asOf': stamp, 'warnings': list(warnings),
                   'horizons': {h: {**calls[h], 'facts': {k: facts[k] for k in keys[h]}, 'signals': indicators.describe(facts, keys[h])}
                                for h in HORIZONS}}
        if macro.get('facts'):
            payload['macro'] = {'facts': macro['facts'], 'signals': indicators.describe(macro['facts'], sorted(macro['facts'])),
                                'calendar': macro.get('calendar', []),
                                'news': [{k: n[k] for k in ('date', 'publisher', 'title', 'excerpt')} for n in macro.get('news', [])]}
        progress.role('explainer', 'running')
        try:
            explained = chat(config['model'], payload, config['temperature'])
            progress.role('explainer', 'done')
        except AnalysisError as exc:
            # The calls never depended on the model, so a failed explanation only leaves the code's own text.
            progress.role('explainer', 'failed', str(exc))
            warnings.append(f'{exc} 판정은 코드가 낸 그대로입니다.')

    sources = [{'title': 'Binance 공개 API 스냅샷 · 서버 계산 지표 · 코드 규칙 판정', 'url': None, 'asOf': stamp}]
    if explained and macro.get('facts'):
        sources.append({'title': macro_sources_title(macro), 'url': None, 'asOf': stamp})
        sources += [{'title': f"{n['publisher']} · {n['title']}"[:200], 'url': n['url'], 'asOf': n['date'] + 'T00:00:00+00:00'}
                    for n in macro.get('news', [])[:10]]
    horizons = {}
    for h, (label, frames, _) in HORIZONS.items():
        call, text = calls[h], explained.get(h)
        verdict = call['verdict']
        code_summary = (f"{call['rule']} 조건이 모두 맞아 {LABELS[verdict]}입니다." if verdict != 'wait'
                        else f"{call['rule']} 조건 중 맞지 않는 것이 있어 관망입니다.")
        risk = text['risk'] if text else '해설 모델을 실행하지 않아 규칙 밖의 위험 설명이 없습니다.'
        if text and numbers_in(text['summary'] + ' ' + text['risk']):
            warnings.append(f'해설 모델({label}): 문장에 검증하지 않은 숫자가 있습니다.')
        agent = {'id': 'rule', 'name': '규칙 판정', 'role': call['rule'], 'verdict': verdict, 'summary': code_summary,
                 'evidence': call['checks'], 'counter': risk, 'invalidation': call['exit'],
                 'model': f'코드 · {signals.VERSION}', 'sources': sources}
        horizons[h] = {'verdict': verdict, 'label': label, 'timeframes': frames, 'rule': call['rule'],
                       'summary': text['summary'] if text else code_summary, 'agreement': ' · '.join(call['checks']),
                       'disagreement': risk, 'condition': call['exit'], 'agents': [agent],
                       'explainer': {'model': config['model']} if text else None}
    return {'schemaVersion': 1, 'id': str(uuid.uuid4()), 'exchange': 'Binance', 'market': 'USDⓈ-M perpetual',
            'symbol': snapshot['symbol'], 'asOf': stamp, 'mode': 'analysis', 'promptVersion': signals.VERSION,
            'origin': 'local-llm', 'warnings': list(dict.fromkeys(warnings)), 'facts': facts, 'horizons': horizons}
