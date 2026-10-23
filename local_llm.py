"""Local LM Studio team: one model per role, cited values verified against server-computed indicators."""
import json
import math
import re
import threading
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import URLError

import indicators
from market import iso

BASE = 'http://127.0.0.1:1234/v1'
CONFIG = Path(__file__).resolve().parent / 'models.json'
ROLES = {'technical': '기술 분석가', 'derivatives': '파생상품 분석가', 'critic': '반대 검토자', 'judge': '종합 판정자'}
HORIZONS = {'intraday': ('단타', '5분 · 15분 · 1시간', ('5m', '15m', '1h')),
            'swing': ('스윙', '4시간 · 1일', ('4h', '1d'))}
PROMPT_VERSION = 'local-team-v1'
lock = threading.Lock()


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
    except (URLError, OSError, ValueError) as exc:
        raise AnalysisError('LM Studio 연결 또는 응답 오류입니다. 포트 1234와 모델 로드 상태를 확인하세요.') from exc


def load_config(path=None):
    try:
        config = json.loads(Path(path or CONFIG).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise AnalysisError('models.json을 읽을 수 없습니다. 파일과 JSON 형식을 확인하세요.') from exc
    roles = config.get('roles') if isinstance(config, dict) else None
    if (not isinstance(roles, dict) or set(roles) != set(ROLES)
            or any(not isinstance(m, str) or not m.strip() or len(m) > 200 for m in roles.values())):
        raise AnalysisError('models.json의 roles에 technical, derivatives, critic, judge 모델 ID가 모두 필요합니다.')
    temperature = config.get('temperature', 0.1)
    if type(temperature) not in (int, float) or not 0 <= temperature <= 1:
        raise AnalysisError('models.json의 temperature는 0~1 사이 숫자여야 합니다.')
    return {'roles': {k: v.strip() for k, v in roles.items()}, 'temperature': float(temperature)}


def status():
    config = load_config()
    result = request('/models')
    if not isinstance(result, dict) or not isinstance(result.get('data'), list) or any(not isinstance(m, dict) for m in result['data']):
        raise AnalysisError('LM Studio 모델 목록 형식이 올바르지 않습니다.')
    ids = {m.get('id') for m in result['data']}
    roles = [{'role': role, 'name': ROLES[role], 'model': model, 'available': model in ids}
             for role, model in config['roles'].items()]
    models = list(config['roles'].values())
    warnings = ['같은 모델을 여러 역할에 쓰고 있어 의견의 독립성이 낮습니다.'] if len(set(models)) < len(models) else []
    return {'endpoint': BASE, 'ready': all(r['available'] for r in roles), 'roles': roles, 'warnings': warnings}


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 300}
VERDICT = {'type': 'string', 'enum': ['long', 'short', 'wait']}
SYNTHESIS = obj({'verdict': VERDICT, **{k: TEXT for k in ('summary', 'agreement', 'disagreement', 'condition')}})
JUDGE_SCHEMA = obj({h: SYNTHESIS for h in HORIZONS})


def analyst_schema(keys):
    # The ref enum lets constrained decoding reject made-up indicator names outright.
    def agent(horizon):
        evidence = obj({'claim': TEXT, 'ref': {'type': 'string', 'enum': keys[horizon]}, 'value': {'type': 'number'}})
        return obj({'verdict': VERDICT, 'summary': TEXT,
                    'evidence': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': evidence},
                    'counter': TEXT, 'invalidation': TEXT})
    return obj({h: agent(h) for h in HORIZONS})


def validate(value, schema):
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            raise AnalysisError('모델 보고서의 필드가 올바르지 않습니다.')
        for key, child in schema['properties'].items():
            validate(value[key], child)
    elif kind == 'array':
        if not isinstance(value, list) or not schema['minItems'] <= len(value) <= schema['maxItems']:
            raise AnalysisError('모델 근거 개수가 올바르지 않습니다.')
        for item in value:
            validate(item, schema['items'])
    elif kind == 'number':
        if type(value) not in (int, float) or not math.isfinite(value):
            raise AnalysisError('모델 근거 값이 숫자가 아닙니다.')
    elif not isinstance(value, str) or not value.strip() or len(value) > schema.get('maxLength', 300):
        raise AnalysisError('모델 보고서의 문장 형식이 올바르지 않습니다.')
    elif 'enum' in schema and value not in schema['enum']:
        raise AnalysisError('모델 판정 또는 참조 값이 올바르지 않습니다.')
    return value


def significant_digits(value):
    number = Decimal(repr(value)).normalize()
    return 0 if number == 0 else len(number.as_tuple().digits)


def matches(cited, actual):
    """Within 0.5%, or the actual value rounded to the cited precision (at least 2 significant digits)."""
    if abs(cited - actual) <= abs(actual) * 0.005:
        return True
    digits = significant_digits(cited)
    return digits >= 2 and float(f'{actual:.{digits}g}') == float(f'{cited:.{digits}g}')


# Interval and indicator names contain digits but are not claims about values.
NAMES = re.compile(r'(?i)\b(?:5m|15m|1h|4h|1d)\b|\b(?:rsi|sma|atr|ema|ma)\s?\d+|\d+\s?(?:분|시간|일)\s?봉?')


def verify(agent, facts):
    checks = []
    for item in agent['evidence']:
        actual = facts.get(item['ref'])
        checks.append({'ref': item['ref'], 'cited': item['value'], 'actual': actual,
                       'ok': actual is not None and matches(item['value'], actual)})
    text = ' '.join([agent['summary'], agent['counter'], agent['invalidation'], *(e['claim'] for e in agent['evidence'])])
    failed = [c for c in checks if not c['ok']]
    return {'checked': len(checks), 'matched': len(checks) - len(failed), 'failed': failed, 'checks': checks,
            'numbersInText': bool(re.search(r'\d', NAMES.sub('', text)))}


COMMON = ('Binance USDⓈ-M 무기한 선물의 공개 시장 데이터를 한국어로 분석합니다. 단타(intraday)는 5m/15m/1h, 스윙(swing)은 4h/1d 지표를 씁니다. '
          '모든 지표는 서버가 마감 봉으로 계산한 facts에 있습니다. 직접 계산하지 말고 facts의 값만 근거로 쓰세요. '
          '근거마다 ref에 facts의 키를, value에 그 키의 값을 그대로 적으세요. 서버가 value를 실제 값과 대조하며, 틀리면 의견이 관망으로 바뀝니다. '
          'claim, summary, counter, invalidation 문장에는 숫자를 쓰지 마세요. 숫자는 value에만 적습니다. '
          '근거가 부족하거나 지표가 엇갈리면 wait. 확률, 목표가, 수익 보장, 주문 지시를 하지 마세요. 문장은 80자 이내로 씁니다.')
ROLE_PROMPTS = {
    'technical': '역할: 기술 분석가. 이동평균 대비 위치, RSI, ATR, 변화율, 20봉 고저, 거래량 비율로 추세와 모멘텀을 판단합니다.',
    'derivatives': ('역할: 파생상품 분석가. 펀딩비율, 마크-인덱스 프리미엄, 미결제약정과 거래량 비율로 수급을 판단합니다. '
                    '펀딩과 미결제약정의 변화 이력은 없으므로 단일 값으로 신규 유입, 청산, 롱 쏠림을 단정하지 마세요.'),
    'critic': ('역할: 반대 검토자. team에 있는 다른 분석가 의견의 약점, 서로 맞지 않는 지표, 놓친 위험을 찾습니다. '
               '같은 facts로 반론 근거를 제시하고, 동의하더라도 남은 위험을 적으세요.'),
    'judge': ('역할: 종합 판정자. team의 의견을 정리해 종합 판정, 공통 의견, 남은 이견, 재검토 조건을 씁니다. '
              'verificationFailed가 true인 의견은 신뢰하지 마세요. 새 근거나 숫자를 만들지 마세요. 숫자는 쓰지 않습니다. '
              '의견이 갈리거나 근거가 약하면 wait. 확률, 목표가, 주문 지시를 하지 마세요.'),
}
ROLE_LABELS = {'technical': '추세 · 모멘텀 · 지표', 'derivatives': '펀딩 · 프리미엄 · 미결제약정', 'critic': '반론 · 실패 조건'}


def chat(role, model, payload, schema, temperature):
    system = (ROLE_PROMPTS[role] if role == 'judge' else COMMON + ' ' + ROLE_PROMPTS[role])
    if 'qwen' in model.lower():
        system += ' /no_think'
    label = f'{ROLES[role]}({model})'
    try:
        result = request('/chat/completions', {
            'model': model, 'temperature': temperature, 'max_tokens': 4000, 'stream': False,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            'response_format': {'type': 'json_schema', 'json_schema': {'name': role, 'strict': True, 'schema': schema}},
        })
    except AnalysisError as exc:
        raise AnalysisError(f'{label} 호출 실패: {exc}') from exc
    try:
        choice = result['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise AnalysisError(f'{label} 응답이 끝나지 않아 보고서를 사용하지 않았습니다.')
        return validate(json.loads(choice['message']['content']), schema)
    except AnalysisError as exc:
        raise AnalysisError(str(exc) if label in str(exc) else f'{label}: {exc}') from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AnalysisError(f'{label}이 유효한 JSON 보고서를 반환하지 않았습니다.') from exc


def evidence_text(item, check):
    if check['ok']:
        return f"{item['claim']} ({item['ref']} = {item['value']} · 일치)"
    actual = '없음' if check['actual'] is None else check['actual']
    return f"{item['claim']} ({item['ref']} 인용 {item['value']} · 실제 {actual} · 불일치)"


def analyze(snapshot, config=None):
    config = config or load_config()
    roles = config['roles']
    facts = indicators.flatten(snapshot.get('indicators') or indicators.compute(snapshot))
    keys = {h: sorted(k for k in facts if k.startswith('market.') or k.split('.')[0] in frames)
            for h, (_, _, frames) in HORIZONS.items()}
    stamp = iso(snapshot['serverTime'])
    data = {'symbol': snapshot['symbol'], 'asOf': stamp, 'warnings': snapshot['warnings'],
            'facts': {h: {k: facts[k] for k in keys[h]} for h in HORIZONS}}
    schema = analyst_schema(keys)

    outputs, checks = {}, {}

    def run(role, payload):
        outputs[role] = chat(role, roles[role], payload, schema, config['temperature'])
        checks[role] = {h: verify(outputs[role][h], facts) for h in HORIZONS}

    def effective(role, h):
        return 'wait' if checks[role][h]['failed'] else outputs[role][h]['verdict']

    def team_view(role_list):
        return {h: [{'role': ROLES[r], 'verdict': effective(r, h), 'summary': outputs[r][h]['summary'],
                     'counter': outputs[r][h]['counter'], 'verificationFailed': bool(checks[r][h]['failed'])}
                    for r in role_list] for h in HORIZONS}

    run('technical', data)
    run('derivatives', data)
    run('critic', {**data, 'team': team_view(('technical', 'derivatives'))})
    judge_input = {'symbol': snapshot['symbol'], 'asOf': stamp, 'warnings': snapshot['warnings'],
                   'team': team_view(('technical', 'derivatives', 'critic')),
                   'missing': '뉴스·거시 자료는 연결되지 않아 해당 분석가는 관망입니다.'}
    judged = chat('judge', roles['judge'], judge_input, JUDGE_SCHEMA, config['temperature'])

    sources = [{'title': 'Binance 공개 API 스냅샷 · 서버 계산 지표', 'url': None, 'asOf': stamp}]
    warnings = list(snapshot['warnings'])
    horizons = {}
    for h, (label, frames, _) in HORIZONS.items():
        agents = []
        for role in ('technical', 'derivatives', 'critic'):
            out, check = outputs[role][h], checks[role][h]
            agent = {'id': role, 'name': ROLES[role], 'role': ROLE_LABELS[role], 'verdict': effective(role, h),
                     'summary': out['summary'], 'counter': out['counter'], 'invalidation': out['invalidation'],
                     'evidence': [evidence_text(i, c) for i, c in zip(out['evidence'], check['checks'])],
                     'model': roles[role], 'sources': sources, 'verification': {**check, 'originalVerdict': out['verdict']}}
            agents.append(agent)
            if check['failed']:
                warnings.append(f"{ROLES[role]}({label}): 인용 수치 {len(check['failed'])}개 불일치로 관망 처리했습니다.")
            if check['numbersInText']:
                warnings.append(f'{ROLES[role]}({label}): 문장에 검증하지 않은 숫자가 있습니다.')
        agents.insert(2, {'id': 'macro', 'name': '뉴스·거시 분석가', 'role': '자료 미연결', 'verdict': 'wait',
                          'summary': '뉴스·거시 자료가 없어 판정을 보류합니다.', 'evidence': ['뉴스와 경제 일정은 수집하지 않았습니다.'],
                          'counter': '외부 이벤트가 기술적 해석을 바꿀 수 있습니다.', 'invalidation': '시각과 출처가 검증된 자료를 연결한 뒤 재분석합니다.',
                          'model': '미실행 · 자료 부족', 'sources': [{'title': '뉴스·거시 자료 미제공', 'url': None, 'asOf': stamp}]})
        synthesis = judged[h]
        verdict = synthesis['verdict']
        # The judge may only side with a verdict some verified team member actually holds.
        if verdict != 'wait' and verdict not in {a['verdict'] for a in agents}:
            warnings.append(f'종합 판정자({label}): 팀원 누구도 지지하지 않은 판정이라 관망으로 바꿨습니다.')
            verdict = 'wait'
        horizons[h] = {**{k: synthesis[k] for k in ('summary', 'agreement', 'disagreement', 'condition')},
                       'verdict': verdict, 'label': label, 'timeframes': frames, 'agents': agents,
                       'judge': {'model': roles['judge'], 'originalVerdict': synthesis['verdict']}}
    return {'schemaVersion': 1, 'id': str(uuid.uuid4()), 'exchange': 'Binance', 'market': 'USDⓈ-M perpetual',
            'symbol': snapshot['symbol'], 'asOf': stamp, 'mode': 'analysis', 'promptVersion': PROMPT_VERSION,
            'origin': 'local-llm', 'warnings': list(dict.fromkeys(warnings)), 'facts': facts,
            'team': [{'role': r, 'name': ROLES[r], 'model': m} for r, m in roles.items()], 'horizons': horizons}
