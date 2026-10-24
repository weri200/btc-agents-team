"""Verdict journal: stores every finished report and scores its calls against candles collected later."""
from contextlib import closing
from datetime import datetime
import json
import sqlite3
from pathlib import Path

from market import DB, INTERVALS

# How long each horizon's call is held before it is scored, and which stored bars measure it.
# shortcut: fixed holds and close-to-close returns without fees or stops; add those when the rules state them.
HOLDS = {'intraday': ('15m', 4 * 3600000), 'swing': ('4h', 72 * 3600000)}


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, symbol TEXT, as_of INTEGER, prompt_version TEXT, payload TEXT, mode TEXT)')
    if 'mode' not in [c[1] for c in db.execute('PRAGMA table_info(reports)')]:
        db.execute("ALTER TABLE reports ADD COLUMN mode TEXT DEFAULT 'analysis'")  # journals saved before backtests existed
    db.execute('CREATE TABLE IF NOT EXISTS verdicts (report_id TEXT, horizon TEXT, role TEXT, verdict TEXT, PRIMARY KEY(report_id,horizon,role))')
    return db


def save_report(report, path=DB):
    as_of = int(datetime.fromisoformat(report['asOf']).timestamp() * 1000)
    rows = []
    for horizon, data in report['horizons'].items():
        rows.append((report['id'], horizon, 'judge', data['verdict']))
        rows += [(report['id'], horizon, agent['id'], agent['verdict']) for agent in data['agents']]
    with closing(connect(path)) as db, db:
        db.execute('INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?)',
                   (report['id'], report['symbol'], as_of, report['promptVersion'], json.dumps(report, ensure_ascii=False),
                    report.get('mode', 'analysis')))
        db.executemany('INSERT OR REPLACE INTO verdicts VALUES (?,?,?,?)', rows)


def close_at(db, symbol, interval, at):
    """Close of the last stored bar that had closed by `at`, if that bar is the one just before `at`."""
    step = INTERVALS[interval]
    opened = (at + 1) // step * step - step
    row = db.execute('SELECT payload FROM candles WHERE symbol=? AND interval=? AND open_time=?', (symbol, interval, opened)).fetchone()
    return float(json.loads(row[0])['close']) if row else None


def scores(path=DB, now=None):
    """Per mode (live analysis or backtest), prompt version, horizon and role: call counts, and hit rate and mean return of long/short calls."""
    now = now or int(datetime.now().timestamp() * 1000)
    groups = {}
    with closing(connect(path)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS candles (symbol TEXT, interval TEXT, open_time INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,open_time))')
        query = 'SELECT r.symbol, r.as_of, r.mode, r.prompt_version, v.horizon, v.role, v.verdict FROM verdicts v JOIN reports r ON r.id = v.report_id'
        for symbol, as_of, mode, version, horizon, role, verdict in db.execute(query).fetchall():
            interval, hold = HOLDS[horizon]
            g = groups.setdefault((mode, version, horizon, role), {'mode': mode, 'promptVersion': version, 'horizon': horizon, 'role': role,
                                                             'calls': 0, 'long': 0, 'short': 0, 'wait': 0,
                                                             'pending': 0, 'missing': 0, 'scored': 0, 'hits': 0, 'returns': []})
            g['calls'] += 1
            g[verdict] += 1
            if verdict == 'wait':
                continue
            if as_of + hold > now:
                g['pending'] += 1
                continue
            entry, exit_ = close_at(db, symbol, interval, as_of), close_at(db, symbol, interval, as_of + hold)
            if entry is None or exit_ is None:
                g['missing'] += 1  # The server was not collecting then; the call cannot be scored.
                continue
            ret = (exit_ / entry - 1) * (1 if verdict == 'long' else -1)
            g['scored'] += 1
            g['hits'] += ret > 0
            g['returns'].append(ret)
    result = []
    for g in groups.values():
        returns = g.pop('returns')
        g['waitRate'] = g['wait'] / g['calls']
        g['hitRate'] = g['hits'] / g['scored'] if g['scored'] else None
        g['meanReturnPct'] = sum(returns) / len(returns) * 100 if returns else None
        result.append(g)
    return sorted(result, key=lambda g: (g['mode'], g['promptVersion'], g['horizon'], g['role'] != 'judge', g['role']))
