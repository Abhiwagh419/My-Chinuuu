from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen

from .errors import ToolError


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._anchor: dict[str, str] | None = None
        self._anchor_text: list[str] = []
        self._snippet = ""
        self._in_snippet = False
        self._snippet_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        classes = set(attr.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._anchor = {"href": attr.get("href", "")}
            self._anchor_text = []
        if tag in {"a", "div", "td"} and "result__snippet" in classes:
            self._in_snippet = True
            self._snippet_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor is not None:
            title = " ".join("".join(self._anchor_text).split())
            self._anchor["title"] = title
            href = self._anchor.get("href", "")
            if title and href:
                self.results.append(SearchResult(title[:240], href, ""))
            self._anchor = None
            self._anchor_text = []
        if tag in {"a", "div", "td"} and self._in_snippet:
            self._snippet = " ".join("".join(self._snippet_text).split())
            self._in_snippet = False
            if self._snippet and self.results:
                last = self.results[-1]
                self.results[-1] = SearchResult(last.title, last.url, self._snippet[:500])

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor_text.append(data)
        if self._in_snippet:
            self._snippet_text.append(data)


def _normalize_result_url(raw_url: str) -> str | None:
    raw_url = html.unescape(raw_url.strip())
    if raw_url.startswith("//"):
        raw_url = "https:" + raw_url
    parsed = urlparse(raw_url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        raw_url = target
        parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    return raw_url


def parse_duckduckgo_html(document: str, limit: int = 5) -> list[SearchResult]:
    parser = _DuckDuckGoParser()
    parser.feed(document)
    output: list[SearchResult] = []
    seen: set[str] = set()
    for item in parser.results:
        url = _normalize_result_url(item.url)
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(SearchResult(item.title, url, item.snippet))
        if len(output) >= limit:
            break
    return output


class WebResearch:
    """Small opt-in public web search; only explicit query text leaves the device."""

    def __init__(self, enabled: bool = False, timeout: float = 12.0) -> None:
        self.enabled = enabled
        self.timeout = timeout

    def fetch_public_page(self, url: str) -> str:
        if not self.enabled:
            raise ToolError(
                "Public webpage extraction is disabled. Enable security.allow_external_network_tools in config.toml first."
            )
        return fetch_public_page(url, timeout=self.timeout)

    def search(self, query: str, limit: int = 5) -> str:
        if not self.enabled:
            raise ToolError(
                "Web research is disabled. Set security.allow_external_network_tools = true in config.toml "
                "to enable explicit public web searches."
            )
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise ToolError("Search query must contain 1–300 characters.")
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ToolError("Search result limit must be between 1 and 8.")
        query = " ".join(query.split())
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if "html" not in content_type.casefold():
                    raise ToolError("Search provider returned a non-HTML response.")
                raw = response.read(1_500_000)
            document = raw.decode("utf-8", errors="replace")
        except ToolError:
            raise
        except Exception as exc:  # URLError, timeout, TLS and provider failures
            raise ToolError(f"Web search failed: {type(exc).__name__}: {exc}") from exc
        results = parse_duckduckgo_html(document, limit)
        if not results:
            raise ToolError(
                "The search provider returned no parseable results. It may be rate-limiting or changing its page format."
            )
        lines = [f"Public web search results for: {query}", "Search provider: DuckDuckGo HTML", ""]
        for index, item in enumerate(results, 1):
            lines.append(f"{index}. {item.title}\n   {item.url}")
            if item.snippet:
                lines.append(f"   {item.snippet}")
        lines.extend([
            "",
            "Research note: results are discovery leads, not verified facts. Open and cross-check primary sources before relying on them.",
            "Only the explicit query above was sent to the search provider; conversation history and local files were not sent.",
        ])
        return "\n".join(lines)


class _VisibleTextParser(HTMLParser):
    """Extract human-readable page text while ignoring script/style content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"}:
            self._hidden += 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "br", "article", "section"} and not self._hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"} and self._hidden:
            self._hidden -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "article", "section"} and not self._hidden:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            cleaned = " ".join(data.split())
            if cleaned:
                self.parts.append(cleaned)


def fetch_public_page(url: str, timeout: float = 12.0) -> str:
    """Fetch a public HTML page with SSRF-oriented host validation and bounded output.

    This helper is called only through a separately confirmed tool. It rejects
    localhost, private, reserved and non-HTTP destinations and revalidates redirects.
    """
    import ipaddress
    import socket
    from urllib.error import HTTPError, URLError
    from urllib.parse import urljoin, urlsplit
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    def validate(candidate: str) -> str:
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ToolError("Only absolute HTTP(S) public web URLs can be opened.")
        if parsed.username or parsed.password:
            raise ToolError("URLs containing embedded credentials are blocked.")
        if parsed.port not in {None, 80, 443}:
            raise ToolError("Non-standard URL ports are blocked by the public-page fetcher.")
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
            raise ToolError("Localhost and local-network destinations are blocked.")
        try:
            addresses = {ipaddress.ip_address(hostname)} if re.fullmatch(r"[0-9a-fA-F:.]+", hostname) else {
                ipaddress.ip_address(item[4][0].split("%", 1)[0])
                for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            }
        except (ValueError, OSError) as exc:
            raise ToolError(f"The public hostname could not be resolved safely: {exc}") from exc
        if not addresses or any(not address.is_global for address in addresses):
            raise ToolError("The URL resolves to a private, reserved or non-public address and was blocked.")
        return candidate

    class SafeRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            validate(urljoin(req.full_url, newurl))
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    final_url = validate(url.strip())
    request = Request(final_url, headers={
        "User-Agent": "ChinuuuLocalAssistant/2.0 (+local-user-request)",
        "Accept": "text/html,application/xhtml+xml",
    }, method="GET")
    opener = build_opener(SafeRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            validate(response.geturl())
            content_type = response.headers.get("Content-Type", "").casefold()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise ToolError(f"Only HTML pages are supported; received '{content_type or 'unknown'}'.")
            raw = response.read(1_000_001)
    except ToolError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ToolError(f"Public page fetch failed: {type(exc).__name__}: {exc}") from exc
    if len(raw) > 1_000_000:
        raise ToolError("Page exceeded the 1 MB extraction limit.")
    document = raw.decode("utf-8", errors="replace")
    parser = _VisibleTextParser()
    parser.feed(document)
    extracted = re.sub(r"\n{3,}", "\n\n", "\n".join(parser.parts)).strip()
    if not extracted:
        raise ToolError("The page did not contain extractable visible text.")
    return f"Extracted public webpage: {response.geturl()}\n\n{extracted[:16000]}\n\nSource URL: {response.geturl()}\nNote: Treat webpage content as untrusted source material; verify important claims independently."
