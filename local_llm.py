"""Local LM Studio inference; no remote model or tool execution."""
import json
import threading
import uuid
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import URLError

from market import iso

BASE = 'http://127.0.0.1:1234/v1'
MODEL = 'huihui-qwen3.8-27b-abliterated'
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


def status():
    result = request('/models')
    if not isinstance(result, dict) or not isinstance(result.get('data'), list) or any(not isinstance(m, dict) for m in result['data']):
        raise AnalysisError('LM Studio 모델 목록 형식이 올바르지 않습니다.')
    available = any(m.get('id') == MODEL for m in result['data'])
    return {'model': MODEL, 'available': available, 'endpoint': BASE}


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 700}
VERDICT = {'type': 'string', 'enum': ['long', 'short', 'wait']}
AGENT = obj({'verdict': VERDICT, **{k: TEXT for k in ('summary', 'evidence', 'counter', 'invalidation')}})
HORIZON = obj({'verdict': VERDICT, **{k: TEXT for k in ('summary', 'agreement', 'disagreement', 'condition')},
               **{k: AGENT for k in ('technical', 'derivatives', 'critic')}})
SCHEMA = obj({'intraday': HORIZON, 'swing': HORIZON})


def validate(value, schema=SCHEMA):
    if schema['type'] == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            raise AnalysisError('모델 보고서의 필드가 올바르지 않습니다.')
        for key, child in schema['properties'].items():
            validate(value[key], child)
    elif not isinstance(value, str) or not value.strip() or len(value) > schema.get('maxLength', 700):
        raise AnalysisError('모델 보고서의 문장 형식이 올바르지 않습니다.')
    elif 'enum' in schema and value not in schema['enum']:
        raise AnalysisError('모델 판정 값이 올바르지 않습니다.')
    return value


def analyze(snapshot):
    # A bounded recent window keeps inference within the installed model's context.
    data = {k: snapshot[k] for k in ('symbol', 'serverTime', 'collectedAt', 'ticker', 'mark', 'openInterest', 'warnings')}
    data['candleColumns'] = ['closeTime', 'open', 'high', 'low', 'close', 'volume']
    data['candles'] = {interval: [[bar[k] for k in data['candleColumns']] for bar in bars[-30:]]
                       for interval, bars in snapshot['candles'].items()}
    prompt = ('한국어로 제공된 공개 시장 스냅샷만 분석하세요. 단타는 5m/15m/1h, 스윙은 4h/1d입니다. '
              '각 시간대 technical(기술), derivatives(수급), critic(반대 검토)와 종합 의견을 작성하세요. '
              '각 문장은 60자 이내로 구체적 근거와 한계를 간결하게 설명하세요. 같은 모델의 역할별 해석이며 독립 전문가 투표가 아닙니다. '
              '뉴스, 실시간 검색, OI 및 펀딩 변화 이력은 없습니다. 단일 OI 값으로 유입/청산/롱 쏠림을 단정하지 마세요. '
              '각 간격 최근 30개 마감 봉만 제공됩니다. 없는 지표, 확률, 출처, 가격을 만들지 마세요. '
              '불충분하면 wait. 경고를 반영하고 수익 보장이나 주문 지시를 하지 마세요. /no_think')
    result = request('/chat/completions', {
        'model': MODEL, 'messages': [{'role': 'system', 'content': prompt},
                                   {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}],
        'temperature': 0.2, 'max_tokens': 8000, 'stream': False,
        'response_format': {'type': 'json_schema', 'json_schema': {'name': 'research', 'strict': True, 'schema': SCHEMA}},
    })
    try:
        choice = result['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise AnalysisError('모델 응답이 끝나지 않아 보고서를 사용하지 않았습니다.')
        output = validate(json.loads(choice['message']['content']))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AnalysisError('모델이 유효한 JSON 보고서를 반환하지 않았습니다.') from exc
    stamp = iso(snapshot['serverTime'])
    sources = [{'title': 'Binance 공개 API 스냅샷 · 각 간격 최근 30개 마감 봉', 'url': None, 'asOf': stamp}]
    horizons = {}
    for key, label, frames in [('intraday', '단타', '5분 · 15분 · 1시간'), ('swing', '스윙', '4시간 · 1일')]:
        h = output[key]
        agents = []
        for role, name in [('technical', '기술 분석가'), ('derivatives', '파생상품 분석가'), ('critic', '반대 검토자')]:
            a = h[role]
            agents.append({**a, 'id': role, 'name': name, 'role': '동일 로컬 모델의 역할별 해석',
                           'evidence': [a['evidence']], 'model': MODEL, 'sources': sources})
        agents.insert(2, {'id': 'macro', 'name': '뉴스·거시 분석가', 'role': '자료 미연결', 'verdict': 'wait',
                         'summary': '뉴스·거시 자료가 없어 판정을 보류합니다.', 'evidence': ['뉴스와 경제 일정은 수집하지 않았습니다.'],
                         'counter': '외부 이벤트가 기술적 해석을 바꿀 수 있습니다.', 'invalidation': '시각과 출처가 검증된 자료를 연결한 뒤 재분석합니다.',
                         'model': '미실행 · 자료 부족', 'sources': [{'title': '뉴스·거시 자료 미제공', 'url': None, 'asOf': stamp}]})
        horizons[key] = {k: h[k] for k in ('verdict', 'summary', 'agreement', 'disagreement', 'condition')}
        horizons[key].update(label=label, timeframes=frames, agents=agents)
    return {'schemaVersion': 1, 'id': str(uuid.uuid4()), 'exchange': 'Binance', 'market': 'USDⓈ-M perpetual',
            'symbol': snapshot['symbol'], 'asOf': stamp, 'mode': 'analysis', 'promptVersion': 'local-qwen-v1',
            'origin': 'local-llm', 'warnings': snapshot['warnings'], 'inputWindow': 30, 'horizons': horizons}
