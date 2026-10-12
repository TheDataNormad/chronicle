"""A tiny multi-route HTTP server for tests.

Unlike the single-body helper in test_end_to_end_drift, routes here can have
their own status, headers and body, can change between requests, and every
request is counted, so tests can assert exactly how many times (and which
paths) a fetcher hit.
"""

from __future__ import annotations

import http.server
import threading
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager


class Route:
    def __init__(self, body="", status=200, content_type="text/html; charset=utf-8", headers=None):
        self.body = body
        self.status = status
        self.content_type = content_type
        self.headers = headers or {}


@contextmanager
def serve(routes: dict[str, Route | Callable[[int], Route]]):
    """Serve ``routes`` (path -> Route, or a function of the hit number).

    Unknown paths return 404. Yields (base_url, hits) where ``hits`` is a
    Counter of path -> number of requests.
    """
    hits: Counter = Counter()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            full = self.path
            hits[path] += 1
            hits[full] += 1 if full != path else 0
            route = routes.get(full) or routes.get(path)
            if callable(route):
                route = route(hits[path])
            if route is None:
                route = Route("not found", status=404)
            body = route.body if isinstance(route.body, bytes) else route.body.encode()
            self.send_response(route.status)
            self.send_header("Content-Type", route.content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in route.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args, **kwargs):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", hits
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def json_route(payload, **kw) -> Route:
    import json

    return Route(json.dumps(payload), content_type="application/json", **kw)
