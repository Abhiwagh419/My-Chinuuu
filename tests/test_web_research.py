from __future__ import annotations

import io

import pytest

from chinuuu.errors import ToolError
from chinuuu.tools import ToolRegistry
from chinuuu.web_research import WebResearch, parse_duckduckgo_html


def test_parse_duckduckgo_html_extracts_results_and_snippets():
    document = '''
    <div class="result">
      <a class="result__a" href="https://example.com/article">Example Article</a>
      <div class="result__snippet">A short public result snippet.</div>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.org/docs">Documentation</a>
    </div>
    '''
    results = parse_duckduckgo_html(document, limit=5)
    assert len(results) == 2
    assert results[0].title == "Example Article"
    assert results[0].url == "https://example.com/article"
    assert "public result snippet" in results[0].snippet


def test_parse_rejects_non_http_and_duplicate_links():
    document = '''
    <a class="result__a" href="javascript:alert(1)">Bad</a>
    <a class="result__a" href="https://example.com">Good</a>
    <a class="result__a" href="https://example.com">Duplicate</a>
    '''
    results = parse_duckduckgo_html(document)
    assert len(results) == 1
    assert results[0].title == "Good"


def test_web_research_disabled_by_default():
    with pytest.raises(ToolError, match="disabled"):
        WebResearch(enabled=False).search("weather")


def test_tool_registry_registers_public_search_only_when_enabled(tmp_path):
    disabled = ToolRegistry(tmp_path / "w1", allow_external_network=False)
    enabled = ToolRegistry(tmp_path / "w2", allow_external_network=True)
    assert "search_public_web" not in {name for name, _, _ in disabled.descriptions()}
    assert "search_public_web" in {name for name, _, _ in enabled.descriptions()}


def test_search_sends_only_explicit_query_and_formats_sources(monkeypatch):
    response_body = b'''<a class="result__a" href="https://example.com/source">Source page</a>
    <div class="result__snippet">Primary source snippet.</div>'''

    class FakeResponse:
        headers = {"Content-Type": "text/html; charset=utf-8"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit):
            assert limit == 1_500_000
            return response_body

    captured = {}
    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("chinuuu.web_research.urlopen", fake_urlopen)
    result = WebResearch(enabled=True).search("local-first agents")
    assert "Source page" in result
    assert "https://example.com/source" in result
    assert "Only the explicit query above was sent" in result
    assert "local-first+agents" in captured["url"]
