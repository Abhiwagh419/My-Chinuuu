from __future__ import annotations

import html
import os
import re
from pathlib import Path

from .errors import ToolError


class ArtifactGenerator:
    """Create editable documents and slide decks without modifying existing files."""

    def __init__(self, workspace_root: Path) -> None:
        self.root = workspace_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _target(self, raw_path: str, allowed: set[str]) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolError("Provide a workspace-relative output path.")
        candidate = Path(raw_path)
        if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
            raise ToolError("Generated artifacts must use a relative path inside the configured workspace.")
        if not candidate.name or candidate.name in {".", ".."}:
            raise ToolError("Provide a filename for the generated artifact.")
        if candidate.suffix.lower() not in allowed:
            raise ToolError(f"Unsupported output format. Allowed extensions: {', '.join(sorted(allowed))}.")

        # Walk each parent without following symlinks or Windows junctions.
        # Create missing directories one level at a time to keep the output rooted.
        parent = self.root
        for part in candidate.parts[:-1]:
            if part in {"", "."}:
                continue
            parent = parent / part
            is_junction = getattr(parent, "is_junction", None)
            if parent.is_symlink() or (callable(is_junction) and is_junction()):
                raise ToolError("Generated artifacts cannot be written through symbolic links or directory junctions.")
            if parent.exists() and not parent.is_dir():
                raise ToolError(f"Artifact parent is not a directory: {parent}")
            parent.mkdir(exist_ok=True)

        target = parent / candidate.name
        is_junction = getattr(target, "is_junction", None)
        if target.is_symlink() or (callable(is_junction) and is_junction()):
            raise ToolError("Generated artifact targets cannot be symbolic links or junctions.")
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ToolError("Output path escaped the configured workspace.") from exc
        # Reserve atomically. Existing files are never overwritten, even when two
        # concurrent agent calls select the same output path.
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError as exc:
            raise ToolError(f"Output already exists: {target}. Choose a new path; existing artifacts are never overwritten by the generator.") from exc
        except OSError as exc:
            raise ToolError(f"Could not reserve artifact output path: {exc}") from exc
        return target

    def create_document(self, path: str, title: str, content: str) -> str:
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ToolError("Document title must contain 1–200 characters.")
        if not isinstance(content, str) or not content.strip() or len(content) > 100_000:
            raise ToolError("Document content must contain 1–100,000 characters.")
        target = self._target(path, {".md", ".txt", ".html", ".docx"})
        suffix = target.suffix.lower()
        if suffix in {".md", ".txt"}:
            payload = f"# {title.strip()}\n\n{content.strip()}\n" if suffix == ".md" else f"{title.strip()}\n{'=' * min(len(title.strip()), 80)}\n\n{content.strip()}\n"
            try:
                target.write_text(payload, encoding="utf-8", newline="\n")
            except OSError as exc:
                target.unlink(missing_ok=True)
                raise ToolError(f"Document write failed: {exc}") from exc
        elif suffix == ".html":
            payload = (
                "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                f"<meta name='viewport' content='width=device-width, initial-scale=1'><title>{html.escape(title)}</title>"
                "<style>body{max-width:900px;margin:3rem auto;padding:0 1.2rem;font:16px/1.65 system-ui;color:#20242b}"
                "h1{line-height:1.2}pre{white-space:pre-wrap;background:#f3f5f8;padding:1rem;border-radius:8px}</style>"
                f"</head><body><h1>{html.escape(title.strip())}</h1><pre>{html.escape(content.strip())}</pre></body></html>"
            )
            try:
                target.write_text(payload, encoding="utf-8", newline="\n")
            except OSError as exc:
                target.unlink(missing_ok=True)
                raise ToolError(f"HTML document write failed: {exc}") from exc
        else:
            try:
                from docx import Document  # type: ignore[import-not-found]
            except ImportError as exc:
                target.unlink(missing_ok=True)
                raise ToolError("DOCX generation requires python-docx. Install with: python -m pip install -e '.[documents]'.") from exc
            try:
                document = Document()
                document.add_heading(title.strip(), level=0)
                for paragraph in content.strip().split("\n\n"):
                    cleaned = paragraph.strip()
                    if not cleaned:
                        continue
                    if cleaned.startswith("### "):
                        document.add_heading(cleaned[4:], level=3)
                    elif cleaned.startswith("## "):
                        document.add_heading(cleaned[3:], level=2)
                    elif cleaned.startswith("# "):
                        document.add_heading(cleaned[2:], level=1)
                    else:
                        document.add_paragraph(cleaned)
                document.save(target)
            except Exception as exc:
                target.unlink(missing_ok=True)
                raise ToolError(f"DOCX generation failed: {exc}") from exc
        if not target.is_file() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            raise ToolError("Generated document could not be verified.")
        return f"Document created and verified: {target} ({target.stat().st_size:,} bytes)."

    def create_presentation(self, path: str, title: str, outline: str) -> str:
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise ToolError("Presentation title must contain 1–160 characters.")
        if not isinstance(outline, str) or not outline.strip() or len(outline) > 40_000:
            raise ToolError("Presentation outline must contain 1–40,000 characters.")
        sections = [part.strip() for part in re.split(r"(?m)^[ \t]*-{3,}[ \t]*$", outline) if part.strip()]
        if not sections:
            raise ToolError("Outline must contain at least one slide section.")
        if len(sections) > 40:
            raise ToolError("A presentation is limited to 40 slides per generation.")
        target = self._target(path, {".pptx"})
        try:
            from pptx import Presentation  # type: ignore[import-not-found]
            from pptx.util import Pt  # type: ignore[import-not-found]
        except ImportError as exc:
            target.unlink(missing_ok=True)
            raise ToolError("PPTX generation requires python-pptx. Install with: python -m pip install -e '.[documents]'.") from exc

        try:
            presentation = Presentation()
            presentation.core_properties.title = title.strip()
            presentation.core_properties.author = "Chinuuu"
            title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
            if title_slide.shapes.title is not None:
                title_slide.shapes.title.text = title.strip()
            if len(title_slide.placeholders) > 1:
                title_slide.placeholders[1].text = "Prepared locally with Chinuuu"
            for index, section in enumerate(sections, 1):
                lines = [line.strip() for line in section.splitlines() if line.strip()]
                heading = lines[0].lstrip("#*- ")[:180] if lines else f"Slide {index}"
                body_lines = lines[1:] if len(lines) > 1 else []
                slide = presentation.slides.add_slide(presentation.slide_layouts[1])
                if slide.shapes.title is not None:
                    slide.shapes.title.text = heading or f"Slide {index}"
                if len(slide.placeholders) < 2:
                    raise ToolError("The active PowerPoint template does not expose a title-and-content layout.")
                frame = slide.placeholders[1].text_frame
                frame.clear()
                bullet_count = 0
                for line in body_lines:
                    clean_line = re.sub(r"^[-*•]\s*", "", line).strip()
                    if not clean_line:
                        continue
                    paragraph = frame.paragraphs[0] if bullet_count == 0 else frame.add_paragraph()
                    paragraph.text = clean_line[:350]
                    paragraph.level = 0
                    paragraph.font.size = Pt(20)
                    bullet_count += 1
                    if bullet_count >= 12:
                        break
                if bullet_count == 0:
                    frame.paragraphs[0].text = "Add content to this slide in the source outline."
            presentation.save(target)
            if not target.is_file() or target.stat().st_size < 1000:
                raise ToolError("Presentation output could not be verified.")
        except ToolError:
            target.unlink(missing_ok=True)
            raise
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise ToolError(f"PPTX generation failed: {exc}") from exc
        return f"PowerPoint presentation created and verified: {target} ({len(presentation.slides)} slides, {target.stat().st_size:,} bytes)."
