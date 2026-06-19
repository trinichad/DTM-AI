"""Safe server-side fetch of an owner-supplied docs URL → markdown-ish text (D-27).

The ONLY place the backend fetches an arbitrary owner-typed URL, so it is deliberately
paranoid: https only, the resolved address must be public (no loopback/RFC-1918/link-local
— blocks SSRF at the dashboard), redirects re-checked per hop, 2 MB cap, HTML stripped to
readable text. Stdlib-only.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

_MAX_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 5
_TIMEOUT = 20.0


class DocFetchError(Exception):
    pass


def _assert_public_https(url: str) -> str:
    p = urllib.parse.urlparse(url)
    if p.scheme != "https":
        raise DocFetchError("only https:// URLs can be ingested")
    host = p.hostname or ""
    if not host:
        raise DocFetchError("URL has no host")
    try:
        infos = socket.getaddrinfo(host, p.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise DocFetchError(f"cannot resolve {host}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise DocFetchError(f"{host} resolves to a non-public address — refusing (SSRF guard)")
    return host


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        raise _Redirect(newurl)


class _Redirect(Exception):
    def __init__(self, url: str) -> None:
        self.url = url


def fetch_text(url: str) -> dict[str, Any]:
    """Fetch a public https URL and return {'url', 'title', 'text'} (plain readable text)."""
    opener = urllib.request.build_opener(_NoRedirect())
    current = url.strip()
    for _hop in range(_MAX_REDIRECTS + 1):
        _assert_public_https(current)
        req = urllib.request.Request(current, headers={
            "User-Agent": "MSP-AI docs-ingest/1.0", "Accept": "text/html, text/plain, */*"})
        try:
            with opener.open(req, timeout=_TIMEOUT) as resp:
                raw = resp.read(_MAX_BYTES + 1)
                ctype = resp.headers.get("Content-Type", "")
                break
        except _Redirect as r:
            current = urllib.parse.urljoin(current, r.url)
            continue
        except OSError as e:
            raise DocFetchError(f"fetch failed: {e}")
    else:
        raise DocFetchError("too many redirects")
    if len(raw) > _MAX_BYTES:
        raise DocFetchError("document larger than 2 MB — link a more specific page")
    text = raw.decode("utf-8", "replace")
    title = ""
    if "html" in ctype.lower() or text.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
        title, text = _strip_html(text)
    return {"url": current, "title": title.strip(), "text": text.strip()}


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}
    _BLOCK = {"p", "div", "section", "article", "li", "tr", "br",
              "h1", "h2", "h3", "h4", "h5", "h6", "pre", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False
        self._heading: str = ""

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = "#" * int(tag[1]) + " "
            self.out.append("\n\n" + self._heading)
        elif tag in self._BLOCK:
            self.out.append("\n")
            if tag == "li":
                self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = ""
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth and data.strip():
            self.out.append(data)


def _strip_html(html: str) -> tuple[str, str]:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    text = "".join(p.out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return p.title, text
