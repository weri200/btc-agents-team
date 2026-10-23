"""Macro and news context for the news/macro analyst, fetched through OpenBB in the project .venv.

The server stays standard-library only: OpenBB runs in a subprocess (openbb_bridge.py) and its
output is validated here like any other external response. Every failure is reported, never fatal.
"""
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRIDGE = ROOT / 'openbb_bridge.py'
TIMEOUT = 180
NEWS_LIMIT = 8
NEWS_DAYS = 7
CALENDAR_LIMIT = 12
# US releases that move rate expectations and risk appetite; the rest of the calendar is noise here.
MAJOR_EVENTS = ('cpi', 'ppi', 'pce', 'gdp', 'payroll', 'unemployment', 'jobless', 'retail sales', 'ism', 'pmi', 'fomc',
                'fed ', 'interest rate', 'powell', 'consumer confidence', 'consumer sentiment', 'michigan', 'jolts', 'durable goods')
SOURCES = 'OpenBB · CBOE(VIX·S&P500) · Nasdaq(나스닥100·경제 일정·뉴스) · Fed(국채 금리)'


class MacroUnavailable(Exception):
    pass


def python_path():
    configured = os.environ.get('OPENBB_PYTHON')
    if configured:
        return Path(configured)
    return ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def fetch(symbol, as_of_ms):
    python = python_path()
    if not python.exists():
        raise MacroUnavailable('OpenBB 가상환경(.venv)이 없습니다. README의 OpenBB 설치 방법을 확인하세요.')
    try:
        completed = subprocess.run([str(python), str(BRIDGE), symbol, str(int(as_of_ms))], cwd=ROOT,
                                   capture_output=True, timeout=TIMEOUT, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    except subprocess.TimeoutExpired as exc:
        raise MacroUnavailable(f'OpenBB 수집이 {TIMEOUT}초 안에 끝나지 않았습니다.') from exc
    except OSError as exc:
        raise MacroUnavailable('OpenBB 실행에 실패했습니다.') from exc
    lines = completed.stdout.decode('utf-8', 'replace').strip().splitlines()
    # OpenBB may print build messages first; the bridge's JSON is always the last line.
    if completed.returncode != 0 or not lines:
        raise MacroUnavailable('OpenBB 수집 프로세스가 오류로 끝났습니다.')
    try:
        return json.loads(lines[-1])
    except ValueError as exc:
        raise MacroUnavailable('OpenBB 응답이 JSON이 아닙니다.') from exc


def day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError
    return date.fromisoformat(value)


def text(value, limit):
    if not isinstance(value, str):
        raise ValueError
    return ' '.join(value.split())[:limit]


def series(rows, as_of_day):
    """Daily [date, value] rows up to the analysis date, oldest first."""
    if not isinstance(rows, list):
        raise ValueError
    clean = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            raise ValueError
        when = day(row[0])
        values = [float(v) for v in row[1:]]
        if when <= as_of_day and all(v == v and abs(v) != float('inf') for v in values):
            clean.append([when, *values])
    clean.sort(key=lambda r: r[0])
    return clean


def change_pct(rows, lookback, column=1):
    if len(rows) <= lookback or not rows[-1 - lookback][column]:
        return None
    return round((rows[-1][column] / rows[-1 - lookback][column] - 1) * 100, 3)


def build(raw, as_of_ms):
    """Validate bridge output and turn it into citable facts, news, and calendar items."""
    if not isinstance(raw, dict):
        raise MacroUnavailable('OpenBB 응답 형식이 올바르지 않습니다.')
    as_of = datetime.fromtimestamp(as_of_ms / 1000, timezone.utc)
    as_of_day = as_of.date()
    warnings = [w for w in raw.get('warnings', []) if isinstance(w, str)][:10]
    facts, dates = {}, {}
    try:
        for name, label in (('vix', 'vix'), ('spx', 'spx'), ('ndx', 'ndx')):
            rows = series(raw.get('series', {}).get(name, []), as_of_day)
            if len(rows) < 2:
                continue
            dates[label] = rows[-1][0].isoformat()
            if name == 'vix':
                facts['macro.vix'] = round(rows[-1][1], 2)
            for lookback, suffix in ((1, '1d'), (5, '5d')):
                value = change_pct(rows, lookback)
                if value is not None:
                    facts[f'macro.{label}Change{suffix}Pct'] = value
        rows = series(raw.get('treasury', []), as_of_day)
        if rows:
            dates['treasury'] = rows[-1][0].isoformat()
            # Fed rates arrive as fractions (0.0513 = 5.13%); changes are in basis points.
            facts['macro.us2yPct'] = round(rows[-1][1] * 100, 3)
            facts['macro.us10yPct'] = round(rows[-1][2] * 100, 3)
            facts['macro.curve2s10sBp'] = round((rows[-1][2] - rows[-1][1]) * 10000, 1)
            if len(rows) > 5:
                facts['macro.us2yChange5dBp'] = round((rows[-1][1] - rows[-6][1]) * 10000, 1)
                facts['macro.us10yChange5dBp'] = round((rows[-1][2] - rows[-6][2]) * 10000, 1)
        calendar = []
        for item in raw.get('calendar', []):
            moment = datetime.fromisoformat(item['time'])
            if moment.tzinfo is None:
                raise ValueError
            if not as_of - timedelta(hours=24) <= moment <= as_of + timedelta(hours=72):
                continue
            if not any(k in f" {item['event'].lower()} " for k in MAJOR_EVENTS):
                continue
            released = moment <= as_of
            calendar.append({'time': moment.astimezone(timezone.utc).isoformat(), 'event': text(item['event'], 80),
                             'status': '발표됨' if released else '예정',
                             'consensus': text(item.get('consensus', ''), 20), 'previous': text(item.get('previous', ''), 20),
                             # A value for an event after the analysis time would be future information.
                             'actual': text(item.get('actual', ''), 20) if released else ''})
        calendar.sort(key=lambda c: c['time'])
        news = []
        for item in raw.get('news', []):
            when = day(item['date'])
            if not as_of_day - timedelta(days=NEWS_DAYS) <= when <= as_of_day:
                continue
            url = text(item['url'], 500)
            news.append({'date': when.isoformat(), 'title': text(item['title'], 160), 'excerpt': text(item.get('excerpt', ''), 180),
                         'publisher': text(item.get('publisher', ''), 40),
                         'url': url if url.startswith(('https://', 'http://')) else None})
        news.sort(key=lambda n: n['date'], reverse=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise MacroUnavailable('OpenBB 응답에 형식이 맞지 않는 값이 있습니다.') from exc
    news = news[:NEWS_LIMIT]
    for index, item in enumerate(news, 1):
        item['id'] = f'news-{index}'
    for label, value in dates.items():
        if (as_of_day - date.fromisoformat(value)).days > 5:
            warnings.append(f'{label} 최근 값이 {value}로 오래되었습니다.')
    if not facts:
        raise MacroUnavailable('OpenBB에서 거시 지표를 하나도 받지 못했습니다. ' + ' '.join(warnings))
    return {'facts': facts, 'dates': dates, 'calendar': calendar[:CALENDAR_LIMIT], 'news': news, 'warnings': warnings}


def collect(symbol, as_of_ms):
    return build(fetch(symbol, as_of_ms), as_of_ms)


if __name__ == '__main__':
    import time
    print(json.dumps(collect(sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT', time.time() * 1000), ensure_ascii=False, indent=1))
