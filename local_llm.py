"""Local LM Studio team: one model per role, cited values verified against server-computed indicators."""
import json
import math
import re
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

import indicators
from market import iso

BASE = 'http://127.0.0.1:1234/v1'
CONFIG = Path(__file__).resolve().parent / 'models.json'
ROLES = {'technical': '기술 분석가', 'derivatives': '파생상품 분석가', 'macro': '뉴스·거시 분석가',
         'critic': '반대 검토자', 'judge': '종합 판정자'}
OPTIONAL_ROLES = {'macro'}  # Without it (or without OpenBB data) the macro seat stays a fixed wait.
HORIZONS = {'intraday': ('단타', '5분 · 15분 · 1시간', ('5m', '15m', '1h')),
            'swing': ('스윙', '4시간 · 1일', ('4h', '1d'))}
PROMPT_VERSION = 'local-team-v4'
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

    def role(self, role, status):
        with self.guard:
            if self.state.get('state') != 'running':
                return
            self.state['stage'] = 'analyzing'
            for item in self.state['roles']:
                if item['role'] != role:
                    continue
                item['status'] = status
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
    roles = config.get('roles') if isinstance(config, dict) else None
    if (not isinstance(roles, dict) or not set(ROLES) - OPTIONAL_ROLES <= set(roles) <= set(ROLES)
            or any(not isinstance(m, str) or not m.strip() or len(m) > 200 for m in roles.values())):
        raise AnalysisError('models.json의 roles에 technical, derivatives, critic, judge 모델 ID가 필요합니다. macro는 선택입니다.')
    temperature = config.get('temperature', 0.1)
    if type(temperature) not in (int, float) or not 0 <= temperature <= 1:
        raise AnalysisError('models.json의 temperature는 0~1 사이 숫자여야 합니다.')
    # Keep the run order regardless of how the file lists roles.
    return {'roles': {k: roles[k].strip() for k in ROLES if k in roles}, 'temperature': float(temperature)}


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


TEXT = {'type': 'string', 'minLength': 6, 'maxLength': 300}
VERDICT = {'type': 'string', 'enum': ['long', 'short', 'wait']}
SYNTHESIS = obj({'verdict': VERDICT, **{k: TEXT for k in ('summary', 'agreement', 'disagreement', 'condition')}})
JUDGE_SCHEMA = obj({h: SYNTHESIS for h in HORIZONS})

# Which facts each analyst sees, by indicator name. Narrow inputs keep each model in its lane.
DERIVATIVE_FACTS = {'oiChange1Pct', 'oiChange24Pct', 'oiValueUsd', 'longShortRatio', 'longShortRatioChange24',
                    'topLongShortRatio', 'topLongShortRatioChange24', 'takerBuySellRatio', 'takerBuySellAvg24',
                    'fundingRatePct', 'fundingAvgPct', 'fundingMinPct', 'fundingMaxPct', 'fundingPositiveShare',
                    'premiumPct', 'openInterest', 'markPrice', 'indexPrice'}
PRICE_CONTEXT = {'change1Pct', 'change20Pct', 'volumeRatio20', 'lastPrice'}
ROLE_FACTS = {
    'technical': lambda key, name: not key.startswith('macro.') and name not in DERIVATIVE_FACTS,
    'derivatives': lambda key, name: not key.startswith('macro.') and (name in DERIVATIVE_FACTS or name in PRICE_CONTEXT),
    'macro': lambda key, name: key.startswith('macro.'),
    'critic': lambda key, name: True,
}


def analyst_schema(keys, news_ids=None):
    # The ref enum lets constrained decoding reject made-up indicator names outright; news ids work the same way.
    def agent(horizon):
        # ref and value come before claim so the model picks a fact first and then describes it.
        evidence = obj({'ref': {'type': 'string', 'enum': keys[horizon]}, 'value': {'type': 'number'}, 'claim': TEXT})
        fields = {'verdict': VERDICT, 'summary': TEXT,
                  'evidence': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': evidence},
                  'counter': TEXT, 'invalidation': TEXT}
        if news_ids is not None:
            fields['news'] = {'type': 'array', 'minItems': 0, 'maxItems': 3 if news_ids else 0,
                              'items': {'type': 'string', 'enum': news_ids or ['none']}}
        return obj(fields)
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
    elif (not isinstance(value, str) or len(value.strip()) < schema.get('minLength', 1)
          or len(value) > schema.get('maxLength', 300)):
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
NAMES = re.compile(r'(?i)\b(?:5m|15m|1h|4h|1d)\b|\b(?:rsi|sma|atr|ema|ma)\s?\d+|\d+\s?(?:분|시간|일|기간|봉|개|년)\s?봉?'
                   r'|s&p\s?500|나스닥\s?100|2s10s')


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


COMMON = ('모든 문장은 반드시 한국어로 씁니다. 영어 문장을 쓰지 마세요. Binance USDⓈ-M 무기한 선물의 공개 시장 데이터를 분석합니다. 단타(intraday)는 5m/15m/1h, 스윙(swing)은 4h/1d 지표를 씁니다. '
          '모든 지표는 서버가 마감 봉으로 계산한 facts에 있습니다. 직접 계산하지 말고 facts의 값만 근거로 쓰세요. '
          'signals는 서버가 facts의 방향(위/아래, 증가/감소, 우위)을 문장으로 옮긴 것입니다. 방향과 해석은 signals와 반드시 일치시키세요. '
          '근거마다 ref에 facts의 키를, value에 그 키의 값을 그대로 적으세요. 서버가 value를 실제 값과 대조하며, 틀리면 의견이 관망으로 바뀝니다. '
          'claim, summary, counter, invalidation 문장에는 숫자를 쓰지 마세요. 숫자는 value에만 적습니다. '
          '항목: summary는 판단 한 문장, claim은 그 지표가 뜻하는 해석, counter는 이 판단이 틀릴 수 있는 이유, invalidation은 판단을 철회할 관찰 조건입니다. '
          '근거마다 다른 지표를 쓰고 같은 문장을 반복하지 마세요. 판정 단어(long, short, wait)만 적지 마세요. '
          '근거가 부족하거나 지표가 엇갈리면 wait. 확률, 목표가, 수익 보장, 주문 지시를 하지 마세요. 문장은 80자 이내로 씁니다.')
ROLE_PROMPTS = {
    'technical': '역할: 기술 분석가. 이동평균 대비 위치, RSI, ATR, 변화율, 20봉 고저, 거래량 비율로 추세와 모멘텀을 판단합니다.',
    'derivatives': ('역할: 파생상품 분석가. 포지션과 수급으로 판단합니다. 사용할 facts: 펀딩비율과 최근 정산 평균·최소·최대·양수 비율(market.funding*), '
                    '마크-인덱스 프리미엄, 미결제약정 변화율(oiChange1Pct, oiChange24Pct), 전체 계정 롱숏 비율(longShortRatio)과 대형 트레이더 포지션 롱숏 비율(topLongShortRatio) 및 24기간 변화, '
                    '테이커 매수/매도 비율(takerBuySellRatio)과 24기간 평균. 단타는 1h, 스윙은 4h 기간 값을 씁니다. '
                    '가격 변화(change20Pct)와 미결제약정 변화를 함께 보고 해석하되, 신규 유입이나 청산은 가능성으로만 표현하세요.'),
    'critic': ('역할: 반대 검토자. team에 있는 다른 분석가 의견의 약점, 서로 맞지 않는 지표, 놓친 위험을 찾습니다. '
               '같은 facts로 반론 근거를 제시하고, 동의하더라도 남은 위험을 적으세요.'),
    'macro': ('역할: 뉴스·거시 분석가. 미국 2년·10년 국채 금리와 5일 변화(bp), 장단기 금리차, VIX와 변화율, S&P500·나스닥100 변화율로 '
              '위험 선호를 판단합니다. 참고 자료: calendar는 발표됐거나 예정된 미국 주요 경제 지표, news는 코인 관련 기사 제목과 요약입니다. '
              '기사를 근거로 쓰면 그 기사 id를 news에 적으세요. 기사에 없는 사실, 수치, 발표 결과를 만들지 마세요. '
              '기사와 지수는 날짜 단위이고 미국 장 마감 기준이라 최신 상황과 차이가 있을 수 있습니다. '
              '단타는 1일 변화와 가까운 일정, 스윙은 5일 변화와 금리 흐름을 중시합니다. 예정된 지표의 결과는 추측하지 마세요.'),
    'judge': ('역할: 종합 판정자. team의 의견을 정리해 종합 판정, 공통 의견, 남은 이견, 재검토 조건을 씁니다. '
              'verificationFailed가 true인 의견은 신뢰하지 마세요. 새 근거나 숫자를 만들지 마세요. 숫자는 쓰지 않습니다. '
              '의견이 갈리거나 근거가 약하면 wait. 확률, 목표가, 주문 지시를 하지 마세요.'),
}
ROLE_LABELS = {'technical': '추세 · 모멘텀 · 지표', 'derivatives': '펀딩 · 프리미엄 · 미결제약정',
               'macro': '금리 · 위험 선호 · 뉴스', 'critic': '반론 · 실패 조건'}


def request_retry(path, payload):
    # LM Studio can briefly reject a request while JIT swaps models; one retry covers that.
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


def chat(role, model, payload, schema, temperature):
    system = (ROLE_PROMPTS[role] if role == 'judge' else COMMON + ' ' + ROLE_PROMPTS[role])
    if 'qwen' in model.lower():
        system += ' /no_think'
    label = f'{ROLES[role]}({model})'
    try:
        result = request_retry('/chat/completions', {
            # Reasoning models (e.g. Gemma 4) otherwise spend the whole token budget thinking before the JSON.
            'model': model, 'temperature': temperature, 'max_tokens': 4000, 'stream': False, 'reasoning_effort': 'none',
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            'response_format': {'type': 'json_schema', 'json_schema': {'name': role, 'strict': True, 'schema': schema}},
        })
    except AnalysisError as exc:
        raise AnalysisError(f'{label} 호출 실패: {exc}') from exc
    try:
        choice = result['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise AnalysisError(f'{label} 응답이 끝나지 않아 보고서를 사용하지 않았습니다.')
        return tidy(validate(json.loads(choice['message']['content']), schema))
    except AnalysisError as exc:
        raise AnalysisError(str(exc) if label in str(exc) else f'{label}: {exc}') from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AnalysisError(f'{label}이 유효한 JSON 보고서를 반환하지 않았습니다.') from exc


def evidence_text(item, check):
    if check['ok']:
        return f"{item['claim']} ({item['ref']} = {item['value']} · 일치)"
    actual = '없음' if check['actual'] is None else check['actual']
    return f"{item['claim']} ({item['ref']} 인용 {item['value']} · 실제 {actual} · 불일치)"


def macro_sources_title(macro):
    dates = ', '.join(f'{k} {v}' for k, v in macro.get('dates', {}).items())
    return f'OpenBB 거시 지표 · 기준일 {dates}' if dates else 'OpenBB 거시 지표'


def analyze(snapshot, config=None):
    config = config or load_config()
    roles = config['roles']
    # Recompute so derivatives history fetched for this analysis is included.
    facts = indicators.flatten(indicators.compute(snapshot))
    macro = snapshot.get('macro') if isinstance(snapshot.get('macro'), dict) else {}
    macro_ready = 'macro' in roles and bool(macro.get('facts'))
    if macro_ready:
        facts.update(macro['facts'])
    # market.* and macro.* apply to both horizons; interval keys only to their own horizon.
    keys = {role: {h: sorted(k for k in facts if (k.startswith(('market.', 'macro.')) or k.split('.')[0] in frames)
                             and allowed(k, k.split('.', 1)[1]))
                   for h, (_, _, frames) in HORIZONS.items()}
            for role, allowed in ROLE_FACTS.items()}
    stamp = iso(snapshot['serverTime'])
    warnings = (list(snapshot['warnings']) + list((snapshot.get('derivatives') or {}).get('warnings', []))
                + list(macro.get('warnings', [])))
    if 'macro' in roles and not macro_ready:
        warnings.append('뉴스·거시 분석가: ' + (macro.get('error') or 'OpenBB 자료가 없어 실행하지 않았습니다.'))
    data = {'symbol': snapshot['symbol'], 'asOf': stamp, 'warnings': list(warnings)}
    news = {n['id']: n for n in macro.get('news', [])} if macro_ready else {}

    outputs, checks = {}, {}

    def run(role, extra=None, news_ids=None):
        payload = {**data, 'facts': {h: {k: facts[k] for k in keys[role][h]} for h in HORIZONS},
                   'signals': {h: indicators.describe(facts, keys[role][h]) for h in HORIZONS}, **(extra or {})}
        progress.role(role, 'running')
        outputs[role] = chat(role, roles[role], payload, analyst_schema(keys[role], news_ids), config['temperature'])
        progress.role(role, 'done')
        checks[role] = {h: verify(outputs[role][h], facts) for h in HORIZONS}

    def effective(role, h):
        return 'wait' if checks[role][h]['failed'] else outputs[role][h]['verdict']

    def team_view(role_list):
        return {h: [{'role': ROLES[r], 'verdict': effective(r, h), 'summary': outputs[r][h]['summary'],
                     'counter': outputs[r][h]['counter'], 'verificationFailed': bool(checks[r][h]['failed'])}
                    for r in role_list] for h in HORIZONS}

    analysts = ['technical', 'derivatives']
    run('technical')
    run('derivatives')
    if macro_ready:
        run('macro', {'calendar': macro['calendar'],
                      'news': [{k: n[k] for k in ('id', 'date', 'publisher', 'title', 'excerpt')} for n in news.values()]},
            news_ids=list(news))
        analysts.append('macro')
    elif 'macro' in roles:
        progress.role('macro', 'skipped')
    run('critic', {'team': team_view(analysts)})
    judge_input = {'symbol': snapshot['symbol'], 'asOf': stamp, 'warnings': list(warnings),
                   'team': team_view([*analysts, 'critic'])}
    if not macro_ready:
        judge_input['missing'] = '뉴스·거시 자료가 없어 해당 분석가는 관망입니다.'
    progress.role('judge', 'running')
    judged = chat('judge', roles['judge'], judge_input, JUDGE_SCHEMA, config['temperature'])
    progress.role('judge', 'done')

    sources = [{'title': 'Binance 공개 API 스냅샷 · 서버 계산 지표', 'url': None, 'asOf': stamp}]
    horizons = {}
    for h, (label, frames, _) in HORIZONS.items():
        agents = []
        for role in (*analysts, 'critic'):
            out, check = outputs[role][h], checks[role][h]
            agent = {'id': role, 'name': ROLES[role], 'role': ROLE_LABELS[role], 'verdict': effective(role, h),
                     'summary': out['summary'], 'counter': out['counter'], 'invalidation': out['invalidation'],
                     'evidence': [evidence_text(i, c) for i, c in zip(out['evidence'], check['checks'])],
                     'model': roles[role], 'sources': sources, 'verification': {**check, 'originalVerdict': out['verdict']}}
            if role == 'macro':
                cited = [news[i] for i in dict.fromkeys(out['news'])]
                agent['sources'] = [{'title': macro_sources_title(macro), 'url': None, 'asOf': stamp},
                                    *({'title': f"{n['publisher']} · {n['title']}"[:200], 'url': n['url'],
                                       'asOf': n['date'] + 'T00:00:00+00:00'} for n in cited)]
                agent['news'] = [n['id'] for n in cited]
            agents.append(agent)
            if check['failed']:
                warnings.append(f"{ROLES[role]}({label}): 인용 수치 {len(check['failed'])}개 불일치로 관망 처리했습니다.")
            if check['numbersInText']:
                warnings.append(f'{ROLES[role]}({label}): 문장에 검증하지 않은 숫자가 있습니다.')
        if not macro_ready:
            reason = macro.get('error') or ('models.json에 macro 모델이 없습니다.' if 'macro' not in roles else 'OpenBB 자료가 없습니다.')
            agents.insert(2, {'id': 'macro', 'name': '뉴스·거시 분석가', 'role': '자료 미연결', 'verdict': 'wait',
                              'summary': '뉴스·거시 자료가 없어 판정을 보류합니다.', 'evidence': [reason],
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
