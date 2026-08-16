"""Stdlib HTTP client for the acceptance lane (non-test module per suite hygiene):
cookie-jar sessions (the Flask session cookie must ride, or CSRF tokens never match),
CSRF extraction from a rendered form, and redirect-visible POSTs."""
from __future__ import annotations

import http.cookiejar
import re
import urllib.error
import urllib.parse
import urllib.request


class Client:
    def __init__(self, base: str):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect())

    def get(self, path: str, timeout: float = 15.0):
        try:
            r = self.opener.open(self.base + path, timeout=timeout)
            return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def csrf(self, path: str = "/") -> str:
        _status, body = self.get(path)
        m = re.search(r'name="_csrf" value="([^"]+)"', body)
        if not m:
            raise AssertionError(f"no CSRF token on {path}")
        return m.group(1)

    def post(self, path: str, data: dict | None = None, csrf_from: str | None = "/",
             timeout: float = 120.0):
        """POST form data; csrf_from names the page to harvest the token from
        (None = send no token — the negative-case probe)."""
        form = dict(data or {})
        if csrf_from is not None:
            form.setdefault("_csrf", self.csrf(csrf_from))
        body = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(self.base + path, data=body, method="POST")
        try:
            r = self.opener.open(req, timeout=timeout)
            return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep 3xx visible — tests assert the redirect itself, then follow explicitly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
