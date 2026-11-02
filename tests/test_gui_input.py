from types import SimpleNamespace

from chinuuu.gui import ChinuuuDesktopApp


class FakeInput:
    def __init__(self, value=""):
        self.value = value
        self.deleted = False

    def get(self, start, end):
        return self.value

    def delete(self, start, end):
        self.deleted = True
        self.value = ""


def test_enter_sends_and_prevents_text_widget_newline(monkeypatch):
    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.input = FakeInput("hello")
    app.busy = False
    submitted = []
    monkeypatch.setattr(app, "_submit_text", lambda text: (submitted.append(text), setattr(app, "busy", True)))

    result = app._on_enter(SimpleNamespace(state=0))

    assert result == "break"
    assert submitted == ["hello"]
    assert app.input.deleted


def test_shift_enter_inserts_newline_without_sending(monkeypatch):
    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.input = FakeInput("line one")
    app.busy = False
    monkeypatch.setattr(app, "_submit_text", lambda _text: (_ for _ in ()).throw(AssertionError("must not send")))

    result = app._on_enter(SimpleNamespace(state=0x0001))

    assert result is None
    assert not app.input.deleted


def test_send_does_not_clear_input_while_busy(monkeypatch):
    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.input = FakeInput("keep this")
    app.busy = True
    monkeypatch.setattr(app, "_submit_text", lambda _text: (_ for _ in ()).throw(AssertionError("must not submit")))

    app._send()

    assert app.input.value == "keep this"
    assert not app.input.deleted


def test_send_ignores_empty_input(monkeypatch):
    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.input = FakeInput("   ")
    app.busy = False
    monkeypatch.setattr(app, "_submit_text", lambda _text: (_ for _ in ()).throw(AssertionError("must not submit")))

    app._send()

    assert not app.input.deleted


def test_keypad_enter_sends(monkeypatch):
    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.input = FakeInput("from keypad")
    app.busy = False
    submitted = []
    monkeypatch.setattr(app, "_submit_text", lambda text: (submitted.append(text), setattr(app, "busy", True)))

    result = app._on_enter(SimpleNamespace(state=0))

    assert result == "break"
    assert submitted == ["from keypad"]


def test_gui_system_slash_command_uses_local_report_not_llm(monkeypatch):
    from chinuuu import gui
    from types import SimpleNamespace
    import queue

    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.events = queue.Queue()
    app.speak_responses = False
    app.assistant = SimpleNamespace(respond=lambda *args: (_ for _ in ()).throw(AssertionError("LLM must not handle slash command")))
    monkeypatch.setattr(gui, "system_report", lambda: "CPU: test\nRAM: test")

    app._respond_worker("/system")

    kind, payload = app.events.get_nowait()
    assert kind == "response"
    assert payload == "CPU: test\nRAM: test"


def test_gui_time_slash_command_uses_local_report_not_llm(monkeypatch):
    from chinuuu import gui
    from types import SimpleNamespace
    import queue

    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.events = queue.Queue()
    app.speak_responses = False
    app.assistant = SimpleNamespace(respond=lambda *args: (_ for _ in ()).throw(AssertionError("LLM must not handle slash command")))
    monkeypatch.setattr(gui, "local_time_report", lambda: "Local time: test")

    app._respond_worker("/time")

    kind, payload = app.events.get_nowait()
    assert kind == "response"
    assert payload == "Local time: test"


def test_gui_health_slash_command_uses_local_diagnostics(monkeypatch):
    from chinuuu import gui
    from types import SimpleNamespace
    import queue

    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.events = queue.Queue()
    app.speak_responses = False
    app.settings = SimpleNamespace()
    app.store = SimpleNamespace()
    app.registry = SimpleNamespace(schemas=lambda: [1, 2, 3])
    app.assistant = SimpleNamespace(respond=lambda *args: (_ for _ in ()).throw(AssertionError("LLM must not handle /health")))
    monkeypatch.setattr(gui, "run_diagnostics", lambda settings, store, count: f"health: {count} tools")

    app._respond_worker("/health")

    kind, payload = app.events.get_nowait()
    assert kind == "response"
    assert payload == "health: 3 tools"


def test_submit_text_shows_running_cat_until_response(monkeypatch):
    import threading

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            pass

    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.busy = False
    app.send_button = SimpleNamespace(configure=lambda **_kwargs: None)
    app._append = lambda *_args: None
    app._set_status = lambda *_args: None
    shown = []
    app._show_thinking = lambda message: shown.append(message)
    monkeypatch.setattr(threading, "Thread", FakeThread)

    app._submit_text("hello Chinuuu")

    assert app.busy is True
    assert shown == ["Chinuuu is thinking"]


def test_response_event_hides_running_cat():
    import queue

    class FakeRoot:
        def after(self, *_args):
            return "scheduled"

    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.events = queue.Queue()
    app.events.put(("response", "Here is the answer"))
    app.root = FakeRoot()
    app.busy = True
    app.send_button = SimpleNamespace(configure=lambda **_kwargs: None)
    app._append = lambda *_args: None
    app._set_status = lambda *_args: None
    app._restart_wakeword = lambda: None
    hidden = []
    app._hide_thinking = lambda: hidden.append(True)

    app._drain_events()

    assert hidden == [True]
    assert app.busy is False


def test_error_event_hides_running_cat():
    import queue

    class FakeRoot:
        def after(self, *_args):
            return "scheduled"

    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.events = queue.Queue()
    app.events.put(("error", "Model unavailable"))
    app.root = FakeRoot()
    app.busy = True
    app.send_button = SimpleNamespace(configure=lambda **_kwargs: None)
    app._append = lambda *_args: None
    app._set_status = lambda *_args: None
    app._restart_wakeword = lambda: None
    hidden = []
    app._hide_thinking = lambda: hidden.append(True)

    app._drain_events()

    assert hidden == [True]
    assert app.busy is False
