from __future__ import annotations

from types import SimpleNamespace

from chinuuu.gui import ChinuuuDesktopApp


class FakeRoot:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []

    def after(self, delay, callback):
        token = f"after-{len(self.scheduled) + 1}"
        self.scheduled.append((token, delay, callback))
        return token

    def after_cancel(self, token):
        self.cancelled.append(token)


class FakeWidget:
    def __init__(self):
        self.pack_calls = []
        self.pack_forget_calls = 0
        self.configurations = []

    def pack(self, **kwargs):
        self.pack_calls.append(kwargs)

    def pack_forget(self):
        self.pack_forget_calls += 1

    def configure(self, **kwargs):
        self.configurations.append(kwargs)


class FakeCanvas(FakeWidget):
    def __init__(self):
        super().__init__()
        self.operations = []

    def delete(self, *args):
        self.operations.append(("delete", args))

    def create_line(self, *args, **kwargs):
        self.operations.append(("line", args, kwargs))

    def create_oval(self, *args, **kwargs):
        self.operations.append(("oval", args, kwargs))

    def create_polygon(self, *args, **kwargs):
        self.operations.append(("polygon", args, kwargs))


def make_app():
    app = ChinuuuDesktopApp.__new__(ChinuuuDesktopApp)
    app.root = FakeRoot()
    app.thinking_frame = FakeWidget()
    app.thinking_canvas = FakeCanvas()
    app.thinking_label = FakeWidget()
    app._thinking_visible = False
    app._thinking_frame_index = 0
    app._thinking_after_id = None
    app._thinking_message = "Chinuuu is thinking"
    return app


def test_thinking_indicator_is_compact_and_schedules_one_animation():
    app = make_app()

    app._show_thinking("Listening locally")
    app._show_thinking("Chinuuu is thinking")

    assert app._thinking_visible is True
    assert len(app.root.scheduled) == 1
    assert app._thinking_message == "Chinuuu is thinking"
    assert app.thinking_frame.pack_calls == [{"fill": "x", "pady": (4, 0)}]
    assert app.thinking_canvas.operations


def test_animation_callback_replaces_its_own_timer():
    app = make_app()
    app._show_thinking()
    first_token, delay, callback = app.root.scheduled[0]

    assert delay == 85
    callback()

    assert len(app.root.scheduled) == 2
    assert app._thinking_after_id == "after-2"
    assert first_token not in app.root.cancelled


def test_hide_thinking_cancels_pending_callback_and_hides_frame():
    app = make_app()
    app._show_thinking()
    pending = app._thinking_after_id

    app._hide_thinking()

    assert app._thinking_visible is False
    assert app.root.cancelled == [pending]
    assert app._thinking_after_id is None
    assert app.thinking_frame.pack_forget_calls == 1


def test_repeated_show_does_not_duplicate_timer_during_voice_transition():
    app = make_app()
    app._show_thinking("Listening locally")
    original_token = app._thinking_after_id

    app._show_thinking("Chinuuu is thinking")

    assert app._thinking_after_id == original_token
    assert len(app.root.scheduled) == 1



def test_running_cat_uses_soft_kitten_details_and_small_geometry():
    app = make_app()
    app._draw_running_cat(9, 2)

    polygons = [op for op in app.thinking_canvas.operations if op[0] == "polygon"]
    ovals = [op for op in app.thinking_canvas.operations if op[0] == "oval"]
    lines = [op for op in app.thinking_canvas.operations if op[0] == "line"]

    # Two ear shapes plus two pink inner-ear details.
    assert len(polygons) == 4
    # Rounded body/head, eyes, nose and cheeks.
    assert len(ovals) >= 7
    # Track, curled tail and four animated legs.
    assert len(lines) >= 6
    assert any(op[2].get("fill") == "#ff9fbd" for op in polygons)
