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
# Points per call: a wait costs less than a wrong call, so waiting is never free but beats guessing wrong.
POINTS = {'hit': 1.0, 'miss': -1.0, 'wait': -0.25}


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


def outcome(db, symbol, as_of, horizon, verdict, now):
    """How one call came out once its hold is over: wait, missing, or hit/miss with the entry and exit closes and the signed return."""
    interval, hold = HOLDS[horizon]
    if as_of + hold > now:
        return {'status': 'pending'}
    if verdict == 'wait':
        return {'status': 'wait', 'points': POINTS['wait']}
    entry, exit_ = close_at(db, symbol, interval, as_of), close_at(db, symbol, interval, as_of + hold)
    if entry is None or exit_ is None:
        return {'status': 'missing'}  # The server was not collecting then; the call cannot be scored.
    ret = (exit_ / entry - 1) * (1 if verdict == 'long' else -1)
    status = 'hit' if ret > 0 else 'miss'
    return {'status': status, 'entry': entry, 'exit': exit_, 'return': ret, 'points': POINTS[status]}


def ensure_candles(db):
    db.execute('CREATE TABLE IF NOT EXISTS candles (symbol TEXT, interval TEXT, open_time INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,open_time))')


def scores(path=DB, now=None):
    """Per mode (live analysis or backtest), prompt version, horizon and role: call counts, hit rate and mean return of long/short calls,
    and the mean points of every call whose hold is over (waits included at their smaller penalty)."""
    now = now or int(datetime.now().timestamp() * 1000)
    groups = {}
    with closing(connect(path)) as db:
        ensure_candles(db)
        query = 'SELECT r.symbol, r.as_of, r.mode, r.prompt_version, v.horizon, v.role, v.verdict FROM verdicts v JOIN reports r ON r.id = v.report_id'
        for symbol, as_of, mode, version, horizon, role, verdict in db.execute(query).fetchall():
            g = groups.setdefault((mode, version, horizon, role), {'mode': mode, 'promptVersion': version, 'horizon': horizon, 'role': role,
                                                             'calls': 0, 'long': 0, 'short': 0, 'wait': 0,
                                                             'pending': 0, 'missing': 0, 'scored': 0, 'hits': 0, 'returns': [], 'points': []})
            g['calls'] += 1
            g[verdict] += 1
            result = outcome(db, symbol, as_of, horizon, verdict, now)
            if result['status'] in ('pending', 'missing'):
                g[result['status']] += 1
                continue
            g['points'].append(result['points'])
            if result['status'] != 'wait':
                g['scored'] += 1
                g['hits'] += result['status'] == 'hit'
                g['returns'].append(result['return'])
    result = []
    for g in groups.values():
        returns, points = g.pop('returns'), g.pop('points')
        g['waitRate'] = g['wait'] / g['calls']
        g['hitRate'] = g['hits'] / g['scored'] if g['scored'] else None
        g['meanReturnPct'] = sum(returns) / len(returns) * 100 if returns else None
        g['score'] = sum(points) / len(points) if points else None
        result.append(g)
    return sorted(result, key=lambda g: (g['mode'], g['promptVersion'], g['horizon'], g['role'] != 'judge', g['role']))


def backtests(path=DB, now=None, limit=100):
    """Newest backtest reports with each horizon's judge call and how it came out."""
    # shortcut: only the newest `limit` are listed; add paging if people need to browse further back.
    now = now or int(datetime.now().timestamp() * 1000)
    with closing(connect(path)) as db:
        ensure_candles(db)
        total = db.execute("SELECT count(*) FROM reports WHERE mode='backtest'").fetchone()[0]
        rows = db.execute("""SELECT r.id, r.symbol, r.as_of, r.prompt_version, json_extract(r.payload, '$.priceScale'), v.horizon, v.verdict
                             FROM (SELECT * FROM reports WHERE mode='backtest' ORDER BY as_of DESC, id LIMIT ?) r
                             JOIN verdicts v ON v.report_id = r.id AND v.role = 'judge' ORDER BY r.as_of DESC, r.id""", (limit,)).fetchall()
        reports = {}
        for rid, symbol, as_of, version, scale, horizon, verdict in rows:
            entry = reports.setdefault(rid, {'id': rid, 'symbol': symbol, 'asOf': as_of, 'promptVersion': version,
                                             'priceScale': scale, 'horizons': {}})
            entry['horizons'][horizon] = {'verdict': verdict, **outcome(db, symbol, as_of, horizon, verdict, now)}
    return {'total': total, 'reports': list(reports.values())}


def backtest_report(report_id, path=DB, now=None):
    """One saved backtest report, each horizon carrying how its judge call came out; None if there is no such backtest."""
    now = now or int(datetime.now().timestamp() * 1000)
    with closing(connect(path)) as db:
        ensure_candles(db)
        row = db.execute("SELECT symbol, as_of, payload FROM reports WHERE id=? AND mode='backtest'", (report_id,)).fetchone()
        if not row:
            return None
        symbol, as_of, payload = row
        report = json.loads(payload)
        for horizon, data in report['horizons'].items():
            data['outcome'] = outcome(db, symbol, as_of, horizon, data['verdict'], now)
    return report
