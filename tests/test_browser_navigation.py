from __future__ import annotations

from urllib.parse import urlparse, parse_qs

import pytest

from chinuuu.assistant import _handle_deterministic_workspace_request
from chinuuu.desktop import ApplicationLauncher
from chinuuu.errors import ToolError
from chinuuu.tools import ToolRegistry


def _chrome_shortcut(tmp_path):
    root = tmp_path / "Programs"
    root.mkdir()
    shortcut = root / "Google Chrome.lnk"
    shortcut.write_bytes(b"shortcut")
    return root, shortcut


def test_browser_synonym_resolves_google_chrome_shortcut(monkeypatch, tmp_path):
    root, shortcut = _chrome_shortcut(tmp_path)
    monkeypatch.setattr(ApplicationLauncher, "_start_menu_roots", staticmethod(lambda: [root]))
    opened = []
    monkeypatch.setattr("chinuuu.desktop.os.name", "nt")
    monkeypatch.setattr(
        "chinuuu.desktop.os.startfile",
        lambda target, operation="open", arguments=None: opened.append((target, operation, arguments)),
        raising=False,
    )

    launcher = ApplicationLauncher()
    result = launcher.open_url("https://web.whatsapp.com/", browser="chrome")

    assert "https://web.whatsapp.com/" in result
    assert opened == [(str(shortcut.resolve()), "open", "https://web.whatsapp.com/")]


def test_search_url_encodes_query_without_shell_arguments():
    url = ApplicationLauncher.search_url("Acidity solutions")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.google.com"
    assert parse_qs(parsed.query)["q"] == ["Acidity solutions"]


def test_browser_search_natural_language_is_parsed_as_compound_intent(monkeypatch, tmp_path):
    root, shortcut = _chrome_shortcut(tmp_path)
    registry = ToolRegistry(tmp_path / "workspace")
    monkeypatch.setattr(ApplicationLauncher, "_start_menu_roots", staticmethod(lambda: [root]))
    opened = []
    monkeypatch.setattr("chinuuu.desktop.os.name", "nt")
    monkeypatch.setattr(
        "chinuuu.desktop.os.startfile",
        lambda target, operation="open", arguments=None: opened.append((target, operation, arguments)),
        raising=False,
    )

    result = _handle_deterministic_workspace_request(
        "open chrome and search for Acidity solutions", registry, None
    )

    assert "Windows accepted navigation" in result
    assert opened[0][0] == str(shortcut.resolve())
    assert "google.com/search?q=Acidity+solutions" in opened[0][2]


def test_browser_destination_natural_language_opens_whatsapp_web_in_chrome(monkeypatch, tmp_path):
    root, shortcut = _chrome_shortcut(tmp_path)
    registry = ToolRegistry(tmp_path / "workspace")
    monkeypatch.setattr(ApplicationLauncher, "_start_menu_roots", staticmethod(lambda: [root]))
    opened = []
    monkeypatch.setattr("chinuuu.desktop.os.name", "nt")
    monkeypatch.setattr(
        "chinuuu.desktop.os.startfile",
        lambda target, operation="open", arguments=None: opened.append((target, operation, arguments)),
        raising=False,
    )

    result = _handle_deterministic_workspace_request(
        "open chrome and open whatsapp web in it", registry, None
    )

    assert "Windows accepted navigation" in result
    assert opened == [(str(shortcut.resolve()), "open", "https://web.whatsapp.com/")]


def test_open_whatsapp_falls_back_to_official_web_client_when_desktop_missing(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr("chinuuu.desktop.webbrowser.open", lambda url, **kwargs: opened.append(url) or True)
    # Simulate a machine with no native WhatsApp install; never query or launch
    # applications from the test runner's actual Windows catalog.
    monkeypatch.setattr(ApplicationLauncher, "discover_apps", lambda self: {})
    monkeypatch.setattr(ApplicationLauncher, "discover_registered_apps", lambda self, force_refresh=False: {})
    monkeypatch.setattr(ApplicationLauncher, "discover_app_paths", lambda self: {})
    registry = ToolRegistry(tmp_path / "workspace")
    result = _handle_deterministic_workspace_request("open whatsapp", registry, None)
    assert "https://web.whatsapp.com/" in result
    assert opened == ["https://web.whatsapp.com/"]


def test_open_website_natural_language_uses_default_browser(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr("chinuuu.desktop.webbrowser.open", lambda url, **kwargs: opened.append(url) or True)
    registry = ToolRegistry(tmp_path / "workspace")
    result = _handle_deterministic_workspace_request("open whatsapp web", registry, None)
    assert "Opened https://web.whatsapp.com/" in result
    assert opened == ["https://web.whatsapp.com/"]


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "file:///C:/Windows/win.ini",
    "https://user:pass@example.com/",
    "not a url",
])
def test_open_url_rejects_non_public_or_invalid_destinations(url):
    with pytest.raises(ToolError):
        ApplicationLauncher.normalize_url(url)


def test_open_url_tool_is_registered_without_confirmation(tmp_path):
    registry = ToolRegistry(tmp_path / "workspace")
    spec = registry._tools["open_url"]
    assert spec.requires_confirmation is False


def test_microsoft_store_search_is_parsed_and_uses_store_protocol(monkeypatch, tmp_path):
    from chinuuu.assistant import _handle_deterministic_workspace_request

    registry = ToolRegistry(tmp_path / "workspace")
    registry.app_launcher.platform_name = "nt"
    opened = []
    monkeypatch.setattr(
        "chinuuu.desktop.os.startfile",
        lambda uri: opened.append(uri),
        raising=False,
    )

    result = _handle_deterministic_workspace_request(
        "open microsoft store and search for photo editor", registry, None
    )

    assert "Microsoft Store search" in result
    assert opened == ["ms-windows-store://search/?query=photo+editor"]
    assert registry._tools["search_microsoft_store"].requires_confirmation is False


def test_store_search_rejects_empty_or_oversized_queries():
    with pytest.raises(ToolError):
        ApplicationLauncher.store_search_uri(" ")
    with pytest.raises(ToolError):
        ApplicationLauncher.store_search_uri("x" * 201)
