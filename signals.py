"""Trading calls made by code, never by a model: each horizon's rule reads the server facts and returns long, short or wait.
Swing is research.py's 'breakout-volume' rule; intraday is the same rule one step down (1h trend, 15m breakout).
README.md explains why the models no longer decide."""
import journal

VERSION = 'rules-v1'  # saved as each report's promptVersion; bump it whenever a rule changes so scores stay apart
# horizon: (trend frame, breakout frame)
FRAMES = {'intraday': ('1h', '15m'), 'swing': ('1d', '4h')}
NAMES = {'intraday': '1시간 추세 + 15분 20봉 돌파 + 거래량 증가', 'swing': '1일 추세 + 4시간 20봉 돌파 + 거래량 증가'}
VERDICTS = {1: 'long', -1: 'short', 0: 'wait'}


def side(*values):
    """+1 or -1 when every value is on that side of zero, otherwise 0."""
    if all(v is not None and v > 0 for v in values):
        return 1
    if all(v is not None and v < 0 for v in values):
        return -1
    return 0


def trend(f, frame='1d'):
    return side(f.get(f'{frame}.change20Pct'), f.get(f'{frame}.closeVsSma50Pct'))


def breakout(f, trend_frame='1d', frame='4h'):
    """The trend's direction when the last close broke the previous 20 bars' high (up) or low (down) that way, else 0."""
    t, close = trend(f, trend_frame), f.get(f'{frame}.close')
    if close is None:
        return 0
    return t if (t == 1 and close > f.get(f'{frame}.high20', float('inf'))) or (t == -1 and close < f.get(f'{frame}.low20', 0)) else 0


def direction(f, horizon):
    trend_frame, frame = FRAMES[horizon]
    return breakout(f, trend_frame, frame) if f.get(f'{frame}.volumeRatio20', 0) > 1 else 0


def decide(f, horizon):
    return VERDICTS[direction(f, horizon)]


def pct(value):
    return '없음' if value is None else f'{value:+.2f}%'


def checks(f, horizon):
    """Each condition of the rule in words with the value it read, so a reader sees why the call came out as it did."""
    trend_frame, frame = FRAMES[horizon]
    t = trend(f, trend_frame)
    close, high, low, volume = (f.get(f'{frame}.{k}') for k in ('close', 'high20', 'low20', 'volumeRatio20'))
    return [f"{trend_frame} 추세: {({1: '위', -1: '아래', 0: '엇갈림'})[t]} (20기간 변화 {pct(f.get(f'{trend_frame}.change20Pct'))}, "
            f"SMA50 대비 {pct(f.get(f'{trend_frame}.closeVsSma50Pct'))})",
            f"{frame} 20봉 돌파: {'고점 돌파' if close > high else '저점 이탈' if close < low else '없음'} "
            f"(종가 {close:g}, 직전 20봉 고점 {high:g} · 저점 {low:g})" if None not in (close, high, low) else f'{frame} 20봉 고저 자료 없음',
            f"{frame} 거래량: 평소의 {volume:g}배 ({'충분' if volume > 1 else '부족'})" if volume is not None else f'{frame} 거래량 자료 없음']


def exit_plan(f, horizon, verdict):
    """How the call is held and scored (journal.HOLDS and BRACKETS), with the price levels when there is a position."""
    interval, hold = journal.HOLDS[horizon]
    hours = hold // 3600000
    if horizon not in journal.BRACKETS:
        return f'진입 뒤 {hours}시간이 지나면 그때 {interval} 종가로 청산합니다.'
    tp, sl = journal.BRACKETS[horizon]
    plan = f'4시간 ATR {tp:g}배 익절 · {sl:g}배 손절 중 먼저 닿는 쪽, 최대 {hours // 24}일 보유'
    atr, close = f.get('4h.atr14'), f.get(f'{journal.ENTRY}.close')  # the entry the score uses
    if verdict == 'wait' or not atr or not close:
        return plan + '.'
    d = 1 if verdict == 'long' else -1
    return f'{plan} (진입 {close:g} · 익절 {close + d * tp * atr:g} · 손절 {close - d * sl * atr:g}).'
