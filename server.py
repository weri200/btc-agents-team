"""Local GUI + public market data API. Run: python server.py"""
import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from market import Collector, MarketError

DIST = Path(__file__).resolve().parent / 'dist'
ASSETS = { '/' + p.name for p in DIST.iterdir() if p.is_file() and p.suffix in ('.html', '.js', '.css') }
collector = Collector()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def json_response(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            return self.json_response(403, {'error': '로컬 주소로 접속하세요.'})
        parsed = urlsplit(self.path)
        if parsed.path == '/api/market':
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + host for host in hosts}:
                return self.json_response(403, {'error': '외부 페이지에서 수집을 요청할 수 없습니다.'})
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.json_response(403, {'error': '외부 페이지 요청을 거절했습니다.'})
            query = parse_qs(parsed.query, keep_blank_values=True)
            if set(query) != {'symbol'} or len(query['symbol']) != 1:
                return self.json_response(400, {'error': 'symbol 하나를 지정하세요.'})
            try:
                return self.json_response(200, collector.collect(query['symbol'][0]))
            except ValueError as exc:
                return self.json_response(400, {'error': str(exc)})
            except MarketError as exc:
                return self.json_response(502, {'error': str(exc)})
        if parsed.path not in ASSETS | {'/'}:
            return self.json_response(404, {'error': '경로를 찾을 수 없습니다.'})
        return super().do_GET()

    def do_HEAD(self):
        # Static files only; never expose directory listings through HEAD.
        if urlsplit(self.path).path not in ASSETS | {'/'}:
            return self.send_error(404)
        return super().do_HEAD()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8876)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Futures Desk: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
