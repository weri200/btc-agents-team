import unittest
from datetime import datetime, timezone
from urllib.error import URLError

import news

AS_OF = int(datetime(2026, 10, 25, 15, 30, tzinfo=timezone.utc).timestamp() * 1000)


def feed(*items):
    body = ''.join(f'<item><title>{t}</title><link>{link}</link><pubDate>{when}</pubDate>'
                   f'<description><![CDATA[<p>{d} &amp; more</p>]]></description></item>' for t, link, when, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


FEEDS = {
    'CoinDesk': feed(('Bitcoin slides on evacuation news', 'https://www.coindesk.com/a', 'Sun, 25 Oct 2026 15:00:00 +0000', 'BTC fell'),
                     ('Published after the analysis', 'https://www.coindesk.com/b', 'Sun, 25 Oct 2026 16:00:00 +0000', 'future'),
                     ('Three days old', 'https://www.coindesk.com/c', 'Thu, 22 Oct 2026 15:00:00 +0000', 'old'),
                     ('Not a web link', 'javascript:alert(1)', 'Sun, 25 Oct 2026 14:00:00 +0000', 'x'),
                     ('No date', 'https://www.coindesk.com/d', 'soon', 'x')),
    'Cointelegraph': feed(('Bitcoin slides on evacuation news', 'https://cointelegraph.com/a', 'Sun, 25 Oct 2026 14:00:00 +0000', 'dup'),
                          ('ETF outflows ease', 'https://cointelegraph.com/b', 'Sat, 24 Oct 2026 20:00:00 +0900', 'flows'),
                          ('Token X launches presale', 'https://cointelegraph.com/press-releases/x', 'Sun, 25 Oct 2026 13:00:00 +0000', 'paid')),
}


class NewsTest(unittest.TestCase):
    def collect(self, get):
        return news.collect(AS_OF, feeds={k: k for k in (*FEEDS, 'Decrypt')}, get=get)

    def test_only_recent_web_headlines_before_the_call_newest_first(self):
        def get(name):
            if name == 'Decrypt':
                raise URLError('down')
            return FEEDS[name]
        result = self.collect(get)
        self.assertEqual([(i['publisher'], i['title'], i['time']) for i in result['items']],
                         [('CoinDesk', 'Bitcoin slides on evacuation news', '2026-10-25T15:00+00:00'),  # the later copy of a title is dropped
                          ('Cointelegraph', 'ETF outflows ease', '2026-10-24T11:00+00:00')])  # converted to UTC
        self.assertEqual(result['items'][0]['excerpt'], 'BTC fell & more')  # tags and entities removed
        self.assertEqual(result['warnings'], ['Decrypt 뉴스를 받지 못했습니다(URLError).', '협찬·보도자료 등 믿기 어려운 코인 기사 1개를 뺐습니다.'])

    def test_a_broken_feed_only_drops_itself(self):
        result = self.collect(lambda name: b'<html>not rss' if name == 'CoinDesk' else FEEDS.get(name, feed()))
        self.assertEqual([i['publisher'] for i in result['items']], ['Cointelegraph', 'Cointelegraph'])
        self.assertEqual(result['warnings'], ['CoinDesk 뉴스를 받지 못했습니다(ParseError).', '협찬·보도자료 등 믿기 어려운 코인 기사 1개를 뺐습니다.'])

    def test_screen_keeps_named_trusted_newsrooms_with_real_links(self):
        base = {'title': 't', 'publisher': 'Reuters', 'url': 'https://www.reuters.com/markets/x'}
        cases = [(base, True), ({**base, 'publisher': ' CoinDesk '}, True), ({**base, 'publisher': ''}, False),
                 ({**base, 'publisher': 'The Motley Fool'}, False), ({**base, 'url': None}, False),
                 ({**base, 'url': 'javascript:alert(1)'}, False), ({**base, 'url': 'https://www.coindesk.com/sponsored-content/x'}, False),
                 ({**base, 'url': 'https://www.coindesk.com/markets/2026/10/24/bitcoin-pressure'}, True)]  # "pressure" is not a press release
        for item, kept in cases:
            with self.subTest(item=item):
                self.assertEqual(news.screen([item]), ([item], 0) if kept else ([], 1))


if __name__ == '__main__':
    unittest.main()
