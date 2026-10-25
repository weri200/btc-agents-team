"""Coin news from public RSS feeds (no keys), for the explainer's review of what the rule does not see.
Each item keeps its publish time, so the explainer can tell fresh news from old. Every failure is reported, never fatal."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import html
import re
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

FEEDS = {'CoinDesk': 'https://www.coindesk.com/arc/outboundfeeds/rss',
         'Cointelegraph': 'https://cointelegraph.com/rss',
         'Decrypt': 'https://decrypt.co/feed'}
HOURS = 48  # older headlines rarely move the next 4h bar
LIMIT = 15
MAX_BYTES = 3_000_000


def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'FuturesDesk/1.0'}), timeout=10) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('피드 크기 초과')
    return raw


def text(value, limit):
    """Plain text: tags, entities and runs of whitespace removed."""
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', value or '')).split())[:limit]


def parse(raw, publisher, as_of):
    """Items published in the HOURS before `as_of` (never after it), newest first."""
    items = []
    for item in ET.fromstring(raw).iter('item'):
        try:
            when = parsedate_to_datetime(item.findtext('pubDate') or '')
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            continue
        link = (item.findtext('link') or '').strip()
        if not as_of - timedelta(hours=HOURS) <= when <= as_of or not link.startswith(('https://', 'http://')):
            continue
        items.append({'time': when.astimezone(timezone.utc).isoformat(timespec='minutes'), 'publisher': publisher,
                      'title': text(item.findtext('title'), 160), 'excerpt': text(item.findtext('description'), 240), 'url': link[:500]})
    return items


# Newsrooms with editors and a corrections policy. Left out on purpose: stock-picking columns and promotional writers
# (The Motley Fool, Zacks, InvestorPlace), auto-generated flow notes (BNK Invest) and contributor networks.
TRUSTED = {'coindesk', 'cointelegraph', 'decrypt', 'the block', 'reuters', 'bloomberg', 'associated press', 'cnbc',
           'the wall street journal', 'financial times', "barron's", 'marketwatch', 'nasdaq'}
PAID = re.compile(r'/(?:sponsored|sponsored-content|partner-content|press-releases?|pr)/', re.I)


def screen(items):
    """(kept, dropped): only headlines with a named trusted publisher and a web link that is not paid or a press release."""
    kept = [i for i in items if (i.get('publisher') or '').strip().lower() in TRUSTED
            and (i.get('url') or '').startswith(('https://', 'http://')) and not PAID.search(i['url'])]
    return kept, len(items) - len(kept)


def collect(as_of_ms, feeds=FEEDS, get=fetch):
    """{'items': newest LIMIT items across feeds, one per title, 'warnings': [why a feed was left out]}."""
    as_of = datetime.fromtimestamp(as_of_ms / 1000, timezone.utc)
    items, warnings = [], []
    for publisher, url in feeds.items():
        try:
            items += parse(get(url), publisher, as_of)
        except Exception as exc:  # a dead or changed feed only drops its own headlines
            warnings.append(f'{publisher} 뉴스를 받지 못했습니다({type(exc).__name__}).')
    items, dropped = screen(items)  # before the limit, so paid items do not take the slots of real news
    if dropped:
        warnings.append(f'협찬·보도자료 등 믿기 어려운 코인 기사 {dropped}개를 뺐습니다.')
    seen, unique = set(), []
    for item in sorted(items, key=lambda i: i['time'], reverse=True):
        key = item['title'].lower()
        if item['title'] and key not in seen:
            seen.add(key)
            unique.append(item)
    return {'items': unique[:LIMIT], 'warnings': warnings}
