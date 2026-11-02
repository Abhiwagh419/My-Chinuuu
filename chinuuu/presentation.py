"""Professional terminal rendering for Chinuuu responses."""

from __future__ import annotations

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text


def assistant_response_panel(response: str) -> Panel:
    """Render a model response as formatted Markdown inside a styled panel.

    Markdown syntax is rendered as typography instead of being shown literally.
    Rich escapes ordinary text, so model-provided text is not treated as Rich markup.
    """
    content = response.strip() or "I received an empty response from the local model."
    rendered = Markdown(
        content,
        code_theme="monokai",
        hyperlinks=True,
        justify="left",
    )
    title = Text("CHINUUU", style="bold bright_cyan")
    return Panel(
        rendered,
        title=title,
        title_align="left",
        border_style="bright_blue",
        padding=(1, 2),
        expand=True,
    )
