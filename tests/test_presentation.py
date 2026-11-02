from io import StringIO

from rich.console import Console

from chinuuu.presentation import assistant_response_panel


def test_markdown_heading_is_rendered_as_heading_not_literal_hashes():
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=90)
    console.print(assistant_response_panel("# System Analysis\n\nThe inspection is complete."))
    rendered = output.getvalue()
    assert "System Analysis" in rendered
    assert "# System Analysis" not in rendered


def test_markdown_bold_and_code_are_rendered_without_raw_markup():
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=90)
    console.print(assistant_response_panel("Use **Python** and run `python main.py`."))
    rendered = output.getvalue()
    assert "Python" in rendered
    assert "python main.py" in rendered
    assert "**Python**" not in rendered
    assert "`python main.py`" not in rendered


def test_empty_response_has_safe_fallback():
    panel = assistant_response_panel("   ")
    assert panel is not None
