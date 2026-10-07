"""
docx_builder.py - Microsoft Word (.docx) Builder for PyBlockRunner

Generates an editable, professionally formatted Word document matching the
Word-style PDF layout:
  - Optional elegant cover page with Student Name and Student ID.
  - Section headers (Word Heading 1 style with accent color and divider rule).
  - Task headings (Word Heading 2 style).
  - Code snippets in clean, shaded monospace callout boxes (when show_code=True).
  - Terminal output rendered as an authentic, dark terminal box with 100% editable text.
  - Matplotlib charts/plots embedded as high-resolution images that can be resized and captioned in Word.
  - All content is fully selectable, searchable, and editable in Microsoft Word, Google Docs, or Pages.
"""

from __future__ import annotations

import datetime
import io
import re
from pathlib import Path
from typing import Optional

from PIL import Image

from .renderer import (
    render_terminal_output,
    render_code_box,
)

try:
    import docx
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
    from docx.oxml import parse_xml, OxmlElement
    from docx.oxml.ns import nsdecls, qn
    _HAS_DOCX = True
except ImportError:
    _HAS_DOCX = False


CONTENT_W = 1096  # Exact same logical content width as pdf_builder


# ---------------------------------------------------------------------------
# Styling constants
# ---------------------------------------------------------------------------

_COLOR_ACCENT_BLUE  = RGBColor(14, 165, 233)   # Sky-500 (#0ea5e9)
_COLOR_DARK_TEXT    = RGBColor(15, 23, 42)     # Slate-900
_COLOR_MUTED_TEXT   = RGBColor(100, 116, 139)  # Slate-500
_COLOR_TERM_PROMPT  = RGBColor(56, 189, 248)   # Sky-400
_COLOR_TERM_STDOUT  = RGBColor(241, 245, 249)  # Slate-100
_COLOR_TERM_STDERR  = RGBColor(248, 113, 113)  # Red-400

_HEX_BG_DARK_TERM   = "0B0F19"                 # Dark terminal background
_HEX_BG_CODE_BOX    = "F8FAFC"                 # Light code box background
_HEX_BORDER_SUBTLE  = "CBD5E1"                 # Slate-300 border
_HEX_ACCENT_LINE    = "0EA5E9"                 # Sky accent rule


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences and XML-illegal control characters from output."""
    clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", clean)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_docx_available() -> bool:
    """Return True if python-docx is installed and available."""
    return _HAS_DOCX


def build_docx(
    results: list,
    output_path: str | Path,
    script_path: Optional[str | Path] = None,
    script_name: str = "script.py",
    theme: str = "light",
    show_code: bool = False,
    author_name: Optional[str] = None,
    author_id: Optional[str] = None,
    no_cover: bool = False,
    font_path: Optional[str] = None,
    terminal_mode: str = "image",
) -> Path:
    """
    Build an editable Microsoft Word (.docx) document from execution results.

    Parameters
    ----------
    results:
        List of :class:`~pyblockrunner.executor.BlockResult` objects.
    output_path:
        Target .docx file path.
    script_path:
        Path to the original Python source file.
    script_name:
        Display name of the Python script.
    theme:
        "light" or "dark".
    show_code:
        Whether to include code snippets in the document.
    author_name:
        Student / Author full name for the cover page.
    author_id:
        Student ID / Registration number for the cover page.
    no_cover:
        If True, skip the formal cover page.
    font_path:
        Custom font path (if applicable).
    terminal_mode:
        "image" (default, exact same terminal output images as PDF) or "styled_text".

    Returns
    -------
    Path
        Resolved path to the saved .docx file.
    """
    if not _HAS_DOCX:
        raise ImportError(
            "python-docx is required for Word export. "
            "Please install it with: pip install python-docx"
        )

    out = Path(output_path)
    if out.suffix.lower() != ".docx":
        out = out.with_suffix(".docx")

    doc = docx.Document()

    # 1. Page Margins (0.75 in on all sides)
    for section in doc.sections:
        section.top_margin = Inches(0.75)
        section.bottom_margin = Inches(0.75)
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)

    total_ms = sum(r.duration_ms for r in results if not r.skipped)
    run_date = datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

    # 2. Cover Page
    if not no_cover:
        _add_cover_page(
            doc=doc,
            script_name=script_name,
            total_blocks=len([r for r in results if not r.skipped]),
            run_date=run_date,
            total_duration_ms=total_ms,
            author_name=author_name,
            author_id=author_id,
        )

    # 3. Content Blocks
    pending_sections: list[str] = []

    for result in results:
        if result.skipped:
            if not result.code.strip():
                continue
            # Non-empty skipped block: render skipped note with terminal image or text
            _add_task_heading(doc, result.title)
            if terminal_mode == "image":
                skip_img = render_terminal_output(
                    stdout="",
                    stderr="[Block skipped via # pyblock: skip]",
                    width_px=CONTENT_W,
                    font_path=font_path,
                    show_prompt=False,
                )
                _add_terminal_image(doc, skip_img)
            else:
                _add_skipped_block(doc, result.title)
            continue

        has_output = bool(result.stdout.strip() or result.stderr.strip())
        has_visual = (show_code and bool(result.code.strip())) or has_output or bool(result.figures)

        if not has_visual:
            # Pure section header without output or code (e.g. "Part 1", "Part 2")
            pending_sections.append(result.title)
            continue

        # Draw any pending section headers above this task
        for sec_title in pending_sections:
            _add_section_heading(doc, sec_title)
        pending_sections.clear()

        # Task Heading
        _add_task_heading(doc, result.title)

        # Code Box (if requested)
        if show_code and result.code.strip():
            if terminal_mode == "image":
                code_img = render_code_box(
                    result.code,
                    theme=theme,
                    width_px=CONTENT_W,
                    font_path=font_path,
                )
                _add_terminal_image(doc, code_img)
            else:
                _add_code_box(doc, result.code)

        # Terminal Output (same terminal images from PDF)
        if has_output:
            if terminal_mode == "image":
                term_img = render_terminal_output(
                    stdout=result.stdout,
                    stderr=result.stderr,
                    width_px=CONTENT_W,
                    font_path=font_path,
                    show_prompt=True,
                    script_path=script_path,
                    script_name=script_name,
                    duration_ms=result.duration_ms,
                )
                _add_terminal_image(doc, term_img)
            else:
                _add_terminal_box(
                    doc=doc,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    script_name=script_name,
                    duration_ms=result.duration_ms,
                )

        # Figures / Matplotlib Plots
        for fig in result.figures:
            _add_terminal_image(doc, fig)

        # Space between blocks
        p_gap = doc.add_paragraph()
        p_gap.paragraph_format.space_before = Pt(4)
        p_gap.paragraph_format.space_after = Pt(8)

    # Flush any trailing pending headers
    if pending_sections:
        for sec_title in pending_sections:
            _add_section_heading(doc, sec_title)
        pending_sections.clear()

    # 4. Combined Terminal Output (if multiple blocks had output)
    blocks_with_output = [
        r for r in results
        if not r.skipped and (r.stdout.strip() or r.stderr.strip())
    ]
    if len(blocks_with_output) > 1:
        combined_stdout = "".join(r.stdout for r in results if not r.skipped and r.stdout)
        combined_stderr = "".join(r.stderr for r in results if not r.skipped and r.stderr)
        if combined_stdout.strip() or combined_stderr.strip():
            doc.add_page_break()
            _add_section_heading(doc, "All Tasks Combined Output")
            if terminal_mode == "image":
                comb_img = render_terminal_output(
                    stdout=combined_stdout,
                    stderr=combined_stderr,
                    width_px=CONTENT_W,
                    font_path=font_path,
                    show_prompt=True,
                    script_path=script_path,
                    script_name=script_name,
                    duration_ms=total_ms,
                )
                _add_terminal_image(doc, comb_img)
            else:
                _add_terminal_box(
                    doc=doc,
                    stdout=combined_stdout,
                    stderr=combined_stderr,
                    script_name=script_name,
                    duration_ms=total_ms,
                )

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out



# ---------------------------------------------------------------------------
# Section & Heading Helpers
# ---------------------------------------------------------------------------

def _add_section_heading(doc: docx.Document, title: str) -> None:
    """Add a prominent Section/Part Heading (Heading 1 equivalent) with accent underline."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True

    run = p.add_run(title)
    run.font.name = "Arial"
    run.font.size = Pt(18)
    run.font.bold = True
    run.font.color.rgb = _COLOR_ACCENT_BLUE

    # Accent underline table rule
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    c = tbl.cell(0, 0)
    c.width = Inches(7.0)
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{_HEX_ACCENT_LINE}"/>')
    c._tc.get_or_add_tcPr().append(shd)
    # 2pt thin line
    tr = tbl.rows[0]._tr.get_or_add_trPr()
    trHeight = parse_xml(f'<w:trHeight {nsdecls("w")} w:val="40" w:hRule="exact"/>')
    tr.append(trHeight)
    # Remove text in rule cell
    c.paragraphs[0].paragraph_format.space_before = Pt(0)
    c.paragraphs[0].paragraph_format.space_after = Pt(0)


def _add_task_heading(doc: docx.Document, title: str) -> None:
    """Add a task/block heading (Heading 2 equivalent) with subtle bottom rule."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True

    run = p.add_run(title)
    run.font.name = "Arial"
    run.font.size = Pt(13)
    run.font.bold = True
    run.font.color.rgb = _COLOR_DARK_TEXT

    # Subtle bottom rule
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    c = tbl.cell(0, 0)
    c.width = Inches(7.0)
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{_HEX_BORDER_SUBTLE}"/>')
    c._tc.get_or_add_tcPr().append(shd)
    tr = tbl.rows[0]._tr.get_or_add_trPr()
    trHeight = parse_xml(f'<w:trHeight {nsdecls("w")} w:val="20" w:hRule="exact"/>')
    tr.append(trHeight)
    c.paragraphs[0].paragraph_format.space_before = Pt(0)
    c.paragraphs[0].paragraph_format.space_after = Pt(0)


# ---------------------------------------------------------------------------
# Code & Terminal Helpers
# ---------------------------------------------------------------------------

def _add_code_box(doc: docx.Document, code: str) -> None:
    """Add an editable, shaded code block in Consolas font."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = tbl.cell(0, 0)
    cell.width = Inches(7.0)

    # Shading and borders
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{_HEX_BG_CODE_BOX}"/>')
    tcPr.append(shd)

    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'  <w:top w:val="single" w:sz="6" w:space="0" w:color="{_HEX_BORDER_SUBTLE}"/>'
        f'  <w:left w:val="single" w:sz="18" w:space="0" w:color="{_HEX_ACCENT_LINE}"/>'
        f'  <w:bottom w:val="single" w:sz="6" w:space="0" w:color="{_HEX_BORDER_SUBTLE}"/>'
        f'  <w:right w:val="single" w:sz="6" w:space="0" w:color="{_HEX_BORDER_SUBTLE}"/>'
        f'</w:tcBorders>'
    )
    tcPr.append(borders)

    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'  <w:top w:w="120" w:type="dxa"/>'
        f'  <w:bottom w:w="120" w:type="dxa"/>'
        f'  <w:left w:w="160" w:type="dxa"/>'
        f'  <w:right w:w="160" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tcPr.append(tcMar)

    cp = cell.paragraphs[0]
    cp.paragraph_format.space_before = Pt(0)
    cp.paragraph_format.space_after = Pt(0)

    # Add code lines
    code_clean = code.strip()
    lines = code_clean.splitlines()
    for i, line in enumerate(lines):
        line_num_run = cp.add_run(f"{i+1:3d}  ")
        line_num_run.font.name = "Consolas"
        line_num_run.font.size = Pt(9)
        line_num_run.font.color.rgb = _COLOR_MUTED_TEXT

        code_run = cp.add_run(line + ("\n" if i < len(lines) - 1 else ""))
        code_run.font.name = "Consolas"
        code_run.font.size = Pt(9.5)
        code_run.font.color.rgb = _COLOR_DARK_TEXT


def _add_terminal_box(
    doc: docx.Document,
    stdout: str,
    stderr: str,
    script_name: str,
    duration_ms: float = 0.0,
) -> None:
    """Add an authentic, pitch-black editable terminal box."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = tbl.cell(0, 0)
    cell.width = Inches(7.0)

    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{_HEX_BG_DARK_TERM}"/>')
    tcPr.append(shd)

    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'  <w:top w:val="single" w:sz="8" w:space="0" w:color="1E293B"/>'
        f'  <w:left w:val="single" w:sz="8" w:space="0" w:color="1E293B"/>'
        f'  <w:bottom w:val="single" w:sz="8" w:space="0" w:color="1E293B"/>'
        f'  <w:right w:val="single" w:sz="8" w:space="0" w:color="1E293B"/>'
        f'</w:tcBorders>'
    )
    tcPr.append(borders)

    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'  <w:top w:w="160" w:type="dxa"/>'
        f'  <w:bottom w:w="160" w:type="dxa"/>'
        f'  <w:left w:w="200" w:type="dxa"/>'
        f'  <w:right w:w="200" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tcPr.append(tcMar)

    cp = cell.paragraphs[0]
    cp.paragraph_format.space_before = Pt(0)
    cp.paragraph_format.space_after = Pt(0)

    # Prompt Header Line
    r_prompt = cp.add_run(f"❯ python {script_name}\n")
    r_prompt.font.name = "Consolas"
    r_prompt.font.size = Pt(9)
    r_prompt.font.bold = True
    r_prompt.font.color.rgb = _COLOR_TERM_PROMPT

    # Output lines
    if stdout:
        clean_out = _strip_ansi(stdout)
        r_stdout = cp.add_run(clean_out.rstrip() + "\n")
        r_stdout.font.name = "Consolas"
        r_stdout.font.size = Pt(9)
        r_stdout.font.color.rgb = _COLOR_TERM_STDOUT

    if stderr:
        clean_err = _strip_ansi(stderr)
        r_stderr = cp.add_run(clean_err.rstrip() + "\n")
        r_stderr.font.name = "Consolas"
        r_stderr.font.size = Pt(9)
        r_stderr.font.color.rgb = _COLOR_TERM_STDERR

    # Footer Duration Line
    dur_str = f"{duration_ms / 1000.0:.2f}s" if duration_ms >= 1000 else f"{int(duration_ms)}ms"
    r_foot = cp.add_run(f"⏱  completed in {dur_str}")
    r_foot.font.name = "Consolas"
    r_foot.font.size = Pt(8)
    r_foot.font.color.rgb = RGBColor(148, 163, 184)


def _add_terminal_image(
    doc: docx.Document,
    img: Image.Image,
    max_width_inches: float = 6.5,
    max_slice_h: int = 1350,
) -> None:
    """
    Insert a screenshot or plot image into Word matching the PDF geometry.
    If the image exceeds single-page printable height (max_slice_h px),
    it is sliced cleanly into vertical page chunks so that Word does not
    clip or truncate the bottom of long terminal outputs.
    """
    # Fit to standard content width if wider
    if img.width > CONTENT_W:
        ratio = CONTENT_W / img.width
        img = img.resize((CONTENT_W, int(img.height * ratio)), Image.LANCZOS)

    if img.height <= max_slice_h:
        _insert_image_run(doc, img, max_width_inches=max_width_inches)
    else:
        # Split tall image into vertical chunks across pages
        y_offset = 0
        total_h = img.height
        while y_offset < total_h:
            slice_h = min(max_slice_h, total_h - y_offset)
            chunk = img.crop((0, y_offset, img.width, y_offset + slice_h))
            is_last = (y_offset + slice_h >= total_h)
            _insert_image_run(
                doc,
                chunk,
                space_before=0 if y_offset > 0 else 4,
                space_after=8 if is_last else 0,
                max_width_inches=max_width_inches,
            )
            y_offset += slice_h


def _insert_image_run(
    doc: docx.Document,
    img: Image.Image,
    space_before: int = 4,
    space_after: int = 8,
    max_width_inches: float = 6.5,
) -> None:
    """Insert a PIL image into a centered paragraph in Word."""
    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    buf.seek(0)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(space_after)

    display_w = min(max_width_inches, img.width / 150.0)
    run = p.add_run()
    run.add_picture(buf, width=Inches(display_w))


_add_figure = _add_terminal_image


def _add_skipped_block(doc: docx.Document, title: str) -> None:
    """Add a skipped block notification."""
    _add_task_heading(doc, title)
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(8)
    r = p.add_run("[Block skipped via user selection]")
    r.font.name = "Arial"
    r.font.size = Pt(10)
    r.font.italic = True
    r.font.color.rgb = _COLOR_MUTED_TEXT


# ---------------------------------------------------------------------------
# Cover Page Helper
# ---------------------------------------------------------------------------

def _add_cover_page(
    doc: docx.Document,
    script_name: str,
    total_blocks: int,
    run_date: str,
    total_duration_ms: float,
    author_name: Optional[str] = None,
    author_id: Optional[str] = None,
) -> None:
    """Add an elegant formal cover page to the Word document."""
    # Top spacing
    p_spacer = doc.add_paragraph()
    p_spacer.paragraph_format.space_before = Pt(40)

    # Header Badge / Organization
    p_head = doc.add_paragraph()
    p_head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_head.paragraph_format.space_after = Pt(4)
    r_sub = p_head.add_run("P Y T H O N   B L O C K   R U N N E R\n")
    r_sub.font.name = "Arial"
    r_sub.font.size = Pt(10)
    r_sub.font.bold = True
    r_sub.font.color.rgb = _COLOR_ACCENT_BLUE

    r_title = p_head.add_run("Code Execution & Verification Dossier")
    r_title.font.name = "Arial"
    r_title.font.size = Pt(24)
    r_title.font.bold = True
    r_title.font.color.rgb = _COLOR_DARK_TEXT

    p_file = doc.add_paragraph()
    p_file.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_file.paragraph_format.space_after = Pt(36)
    r_f = p_file.add_run(f"Script: {script_name}")
    r_f.font.name = "Consolas"
    r_f.font.size = Pt(11)
    r_f.font.color.rgb = _COLOR_MUTED_TEXT

    # Central Credential Showcase Card
    tbl = doc.add_table(rows=2, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

    # Student Name
    c_name = tbl.cell(0, 0)
    c_name.width = Inches(5.5)
    tcPr1 = c_name._tc.get_or_add_tcPr()
    shd1 = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F8FAFC"/>')
    tcPr1.append(shd1)
    b1 = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'  <w:top w:val="single" w:sz="12" w:space="0" w:color="{_HEX_ACCENT_LINE}"/>'
        f'  <w:left w:val="single" w:sz="8" w:space="0" w:color="{_HEX_BORDER_SUBTLE}"/>'
        f'  <w:bottom w:val="single" w:sz="4" w:space="0" w:color="{_HEX_BORDER_SUBTLE}"/>'
        f'  <w:right w:val="single" w:sz="8" w:space="0" w:color="{_HEX_BORDER_SUBTLE}"/>'
        f'</w:tcBorders>'
    )
    tcPr1.append(b1)
    m1 = parse_xml(f'<w:tcMar {nsdecls("w")}><w:top w:w="160" w:type="dxa"/><w:bottom w:w="160" w:type="dxa"/><w:left w:w="200" w:type="dxa"/><w:right w:w="200" w:type="dxa"/></w:tcMar>')
    tcPr1.append(m1)

    pn = c_name.paragraphs[0]
    pn.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rn_lbl = pn.add_run("STUDENT NAME\n")
    rn_lbl.font.name = "Arial"
    rn_lbl.font.size = Pt(10)
    rn_lbl.font.bold = True
    rn_lbl.font.color.rgb = _COLOR_ACCENT_BLUE

    display_name = (author_name or "").strip() or "—"
    rn_val = pn.add_run(display_name)
    rn_val.font.name = "Arial"
    rn_val.font.size = Pt(22)
    rn_val.font.bold = True
    rn_val.font.color.rgb = _COLOR_DARK_TEXT

    # Student ID
    c_id = tbl.cell(1, 0)
    c_id.width = Inches(5.5)
    tcPr2 = c_id._tc.get_or_add_tcPr()
    shd2 = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F1F5F9"/>')
    tcPr2.append(shd2)
    b2 = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'  <w:top w:val="none"/>'
        f'  <w:left w:val="single" w:sz="8" w:space="0" w:color="{_HEX_BORDER_SUBTLE}"/>'
        f'  <w:bottom w:val="single" w:sz="8" w:space="0" w:color="{_HEX_BORDER_SUBTLE}"/>'
        f'  <w:right w:val="single" w:sz="8" w:space="0" w:color="{_HEX_BORDER_SUBTLE}"/>'
        f'</w:tcBorders>'
    )
    tcPr2.append(b2)
    m2 = parse_xml(f'<w:tcMar {nsdecls("w")}><w:top w:w="160" w:type="dxa"/><w:bottom w:w="160" w:type="dxa"/><w:left w:w="200" w:type="dxa"/><w:right w:w="200" w:type="dxa"/></w:tcMar>')
    tcPr2.append(m2)

    pid = c_id.paragraphs[0]
    pid.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rid_lbl = pid.add_run("STUDENT ID\n")
    rid_lbl.font.name = "Arial"
    rid_lbl.font.size = Pt(10)
    rid_lbl.font.bold = True
    rid_lbl.font.color.rgb = _COLOR_ACCENT_BLUE

    display_id = (author_id or "").strip() or "—"
    rid_val = pid.add_run(display_id)
    rid_val.font.name = "Consolas"
    rid_val.font.size = Pt(18)
    rid_val.font.bold = True
    rid_val.font.color.rgb = _COLOR_DARK_TEXT

    # Metadata Footer Block
    p_meta = doc.add_paragraph()
    p_meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_meta.paragraph_format.space_before = Pt(48)
    p_meta.paragraph_format.space_after = Pt(0)

    dur_str = f"{total_duration_ms / 1000.0:.2f}s" if total_duration_ms >= 1000 else f"{int(total_duration_ms)}ms"
    meta_text = (
        f"Execution Timestamp: {run_date}   •   "
        f"Active Blocks: {total_blocks}   •   "
        f"Pipeline Runtime: {dur_str}"
    )
    rm = p_meta.add_run(meta_text)
    rm.font.name = "Arial"
    rm.font.size = Pt(9)
    rm.font.color.rgb = _COLOR_MUTED_TEXT

    doc.add_page_break()
