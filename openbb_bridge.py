"""Fetch macro context through OpenBB and print it as JSON.

Runs inside the project .venv (OpenBB is not a server dependency): `macro.py` starts it as a
subprocess and validates everything it prints. Only providers that need no API key are used.
Usage: python openbb_bridge.py SYMBOL AS_OF_MS
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Crypto ETFs and companies whose Nasdaq news feed carries coin-relevant headlines.
NEWS_SYMBOLS = {'BTCUSDT': ('IBIT', 'COIN'), 'ETHUSDT': ('ETHA', 'IBIT', 'COIN'), 'SOLUSDT': ('BSOL', 'IBIT', 'COIN')}
KEYWORDS = ('bitcoin', 'btc', 'crypto', 'ether', 'ethereum', 'solana', 'stablecoin', 'coinbase', 'blockchain', 'digital asset')
EASTERN = ZoneInfo('America/New_York')  # Nasdaq calendar times are US Eastern.


def closes(frame, column='close'):
    return [[str(index)[:10], float(value)] for index, value in frame[column].dropna().items()]


def main(symbol, as_of_ms):
    as_of = datetime.fromtimestamp(as_of_ms / 1000, timezone.utc)
    start = as_of.date() - timedelta(days=21)
    result = {'series': {}, 'treasury': [], 'calendar': [], 'news': [], 'warnings': []}

    def attempt(name, fn):
        try:
            fn()
        except Exception as exc:  # Each source is optional; report and continue.
            result['warnings'].append(f'{name} 수집 실패: {type(exc).__name__}')

    from openbb import obb

    def indices():
        result['series']['vix'] = closes(obb.cboe.index.historical('VIX', start_date=start).to_df())
        result['series']['spx'] = closes(obb.cboe.index.historical('SPX', start_date=start).to_df())

    def nasdaq100():
        result['series']['ndx'] = closes(obb.nasdaq.index.historical('NDX', start_date=start).to_df())

    def treasury():
        frame = obb.federal_reserve.treasury_rates(start_date=start).to_df()
        result['treasury'] = [[str(i)[:10], float(r['year_2']), float(r['year_10'])]
                              for i, r in frame[['year_2', 'year_10']].dropna().iterrows()]

    def calendar():
        frame = obb.nasdaq.economy.calendar(start_date=as_of.date() - timedelta(days=1),
                                            end_date=as_of.date() + timedelta(days=3)).to_df()
        for index, row in frame.iterrows():
            if row['country'] != 'United States':
                continue
            moment = index.to_pydatetime().replace(tzinfo=EASTERN).astimezone(timezone.utc)
            result['calendar'].append({'time': moment.isoformat(), 'event': str(row['event']),
                                       **{k: str(row[k] or '') for k in ('consensus', 'previous', 'actual')}})

    def news():
        seen = set()
        for ticker in NEWS_SYMBOLS.get(symbol, ('IBIT',)):
            for index, row in obb.news.company(ticker, provider='nasdaq', limit=12).to_df().iterrows():
                text = f"{row['title']} {row.get('excerpt') or ''}".lower()
                if row['url'] in seen or not any(k in text for k in KEYWORDS):
                    continue
                seen.add(row['url'])
                result['news'].append({'date': str(index)[:10], 'title': str(row['title']),
                                       'excerpt': str(row.get('excerpt') or ''), 'publisher': str(row.get('publisher') or ''),
                                       'url': str(row['url'])})

    attempt('CBOE VIX·S&P500', indices)
    attempt('Nasdaq 나스닥100', nasdaq100)
    attempt('Fed 국채 금리', treasury)
    attempt('Nasdaq 경제 일정', calendar)
    attempt('Nasdaq 뉴스', news)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]))
