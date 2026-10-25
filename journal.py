"""Verdict journal: stores every finished report and scores its calls against candles collected later."""
from contextlib import closing
from datetime import datetime
import json
import sqlite3
from pathlib import Path

from market import DB, INTERVALS

# How long each horizon's call is held at most before it is scored, and which stored bars measure it.
# shortcut: returns leave out fees; subtract them if scores are read as account returns.
# Swing exits are found on 1h bars, as research.py finds them, so one rule scores the same in both places.
# 'intraday' only scores reports from before rules-v2, which dropped the intraday call.
HOLDS = {'intraday': ('15m', 4 * 3600000), 'swing': ('1h', 7 * 24 * 3600000)}
# Every call enters at the last 15m close before it, so a live call made mid-way through a 4h bar is not priced at that bar's open.
ENTRY = '15m'
# Swing calls follow research.py's 'breakout-volume' rule: out at 2 x the 4h ATR in profit or 1 x in loss, whichever comes first.
BRACKETS = {'swing': (2.0, 1.0)}
# The 4h ATR the models saw, in real prices (backtest reports saw prices times priceScale).
ATR_SQL = """json_extract(r.payload, '$.facts."4h.atr14"') / coalesce(json_extract(r.payload, '$.priceScale'), 1)"""
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
    # Only the final call is kept: the code's rule makes it, so there are no team votes to score apart.
    rows = [(report['id'], horizon, 'judge', data['verdict']) for horizon, data in report['horizons'].items()]
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


def bracket_exit(db, symbol, interval, as_of, hold, entry, direction, atr, tp, sl):
    """Exit price of a call held with an ATR target and stop: the level the first stored bar reaches (a bar that touches both
    counts as the stop, since one bar cannot tell which came first), else the close when the hold ends.
    Only bars opening at or after `as_of` count: moves before the call are not the call's result.
    None when the ATR or any bar of the hold is missing."""
    step = INTERVALS[interval]
    # shortcut: a live call made mid-bar skips the rest of that bar (under 1h on 1h bars); scan finer bars if that matters.
    first, last = -(-as_of // step) * step, (as_of + hold + 1) // step * step - step
    rows = db.execute('SELECT payload FROM candles WHERE symbol=? AND interval=? AND open_time BETWEEN ? AND ? ORDER BY open_time',
                      (symbol, interval, first, last)).fetchall()
    if not atr or len(rows) != (last - first) // step + 1:
        return None
    target, stop = entry + direction * tp * atr, entry - direction * sl * atr
    for (payload,) in rows:
        bar = json.loads(payload)
        high, low = float(bar['high']), float(bar['low'])
        if (low <= stop) if direction > 0 else (high >= stop):
            return stop
        if (high >= target) if direction > 0 else (low <= target):
            return target
    return float(bar['close'])


def legacy(version):
    """Reports from before rules-v1 (the LLM team) saved only 4h bars for the swing hold."""
    return not version.startswith('rules-')


def outcome(db, symbol, as_of, horizon, verdict, now, atr=None, old=False):
    """How one call came out once its hold is over: wait, missing, or hit/miss with the entry and exit prices and the signed return.
    Horizons in BRACKETS exit at their ATR target or stop (`atr` is the 4h ATR at the call); the rest at the close when the hold ends.
    `old` reports (see legacy) fall back to 4h bars; newer ones are missing without their 1h bars, so one rule is always scored one way."""
    interval, hold = HOLDS[horizon]
    if as_of + hold > now:
        return {'status': 'pending'}
    if verdict == 'wait':
        return {'status': 'wait', 'points': POINTS['wait']}
    direction = 1 if verdict == 'long' else -1
    entry = close_at(db, symbol, ENTRY, as_of)
    if entry is None:
        exit_ = None
    elif horizon in BRACKETS:
        args = (as_of, hold, entry, direction, atr, *BRACKETS[horizon])
        exit_ = bracket_exit(db, symbol, interval, *args) or (bracket_exit(db, symbol, '4h', *args) if old else None)
    else:
        exit_ = close_at(db, symbol, interval, as_of + hold)
    if exit_ is None:
        return {'status': 'missing'}  # The server was not collecting then, or the report has no ATR; the call cannot be scored.
    ret = (exit_ / entry - 1) * direction
    status = 'hit' if ret > 0 else 'miss'
    return {'status': status, 'entry': entry, 'exit': exit_, 'return': ret, 'points': POINTS[status]}


def ensure_candles(db):
    db.execute('CREATE TABLE IF NOT EXISTS candles (symbol TEXT, interval TEXT, open_time INTEGER, payload TEXT, PRIMARY KEY(symbol,interval,open_time))')


def tally(groups, key, verdict, result):
    """Add one call and its outcome to the group under `key`; `key` is a dict of the fields that name the group."""
    g = groups.setdefault(tuple(key.values()), {**key, 'calls': 0, 'long': 0, 'short': 0, 'wait': 0, 'pending': 0, 'missing': 0,
                                                'scored': 0, 'hits': 0, 'returns': [], 'points': []})
    g['calls'] += 1
    g[verdict] += 1
    if result['status'] in ('pending', 'missing'):
        g[result['status']] += 1
        return
    g['points'].append(result['points'])
    if result['status'] != 'wait':
        g['scored'] += 1
        g['hits'] += result['status'] == 'hit'
        g['returns'].append(result['return'])


def summarize(groups):
    result = []
    for g in groups.values():
        returns, points = g.pop('returns'), g.pop('points')
        g['waitRate'] = g['wait'] / g['calls']
        g['hitRate'] = g['hits'] / g['scored'] if g['scored'] else None
        g['meanReturnPct'] = sum(returns) / len(returns) * 100 if returns else None
        g['score'] = sum(points) / len(points) if points else None
        result.append(g)
    return result


def scores(path=DB, now=None):
    """Per mode (live analysis or backtest), prompt version, horizon and role: call counts, hit rate and mean return of long/short calls,
    and the mean points of every call whose hold is over (waits included at their smaller penalty)."""
    now = now or int(datetime.now().timestamp() * 1000)
    groups = {}
    with closing(connect(path)) as db:
        ensure_candles(db)
        query = f'SELECT r.symbol, r.as_of, r.mode, r.prompt_version, v.horizon, v.role, v.verdict, {ATR_SQL} FROM verdicts v JOIN reports r ON r.id = v.report_id'
        for symbol, as_of, mode, version, horizon, role, verdict, atr in db.execute(query).fetchall():
            key = {'mode': mode, 'promptVersion': version, 'horizon': horizon, 'role': role}
            tally(groups, key, verdict, outcome(db, symbol, as_of, horizon, verdict, now, atr, legacy(version)))
    return sorted(summarize(groups), key=lambda g: (g['mode'], g['promptVersion'], g['horizon'], g['role'] != 'judge', g['role']))


def backtests(path=DB, now=None, limit=100):
    """Newest backtest reports with each horizon's judge call and how it came out."""
    # shortcut: only the newest `limit` are listed; add paging if people need to browse further back.
    now = now or int(datetime.now().timestamp() * 1000)
    with closing(connect(path)) as db:
        ensure_candles(db)
        total = db.execute("SELECT count(*) FROM reports WHERE mode='backtest'").fetchone()[0]
        rows = db.execute(f"""SELECT r.id, r.symbol, r.as_of, r.prompt_version, json_extract(r.payload, '$.priceScale'), v.horizon, v.verdict, {ATR_SQL}
                             FROM (SELECT * FROM reports WHERE mode='backtest' ORDER BY as_of DESC, id LIMIT ?) r
                             JOIN verdicts v ON v.report_id = r.id AND v.role = 'judge' ORDER BY r.as_of DESC, r.id""", (limit,)).fetchall()
        reports = {}
        for rid, symbol, as_of, version, scale, horizon, verdict, atr in rows:
            entry = reports.setdefault(rid, {'id': rid, 'symbol': symbol, 'asOf': as_of, 'promptVersion': version,
                                             'priceScale': scale, 'horizons': {}})
            entry['horizons'][horizon] = {'verdict': verdict, **outcome(db, symbol, as_of, horizon, verdict, now, atr, legacy(version))}
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
        atr = report.get('facts', {}).get('4h.atr14')
        atr = atr / (report.get('priceScale') or 1) if atr else None
        for horizon, data in report['horizons'].items():
            data['outcome'] = outcome(db, symbol, as_of, horizon, data['verdict'], now, atr, legacy(report['promptVersion']))
    return report
