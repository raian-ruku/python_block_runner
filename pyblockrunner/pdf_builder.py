"""
pdf_builder.py - Word-doc Style PDF Builder for PyBlockRunner

Layout:
  - Optional cover page (skip with no_cover=True).
  - For each block: bold Word-style heading → optional code box →
    terminal screenshot → inline plot images.
  - Blocks packed vertically on A4 pages; page break only on overflow.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

from .renderer import (
    render_terminal_output,
    render_code_box,
    render_cover_page,
    _load_font,
    _measure,
    _PAGE_BG_DARK,
    _PAGE_BG_LIGHT,
    _ACCENT,
)


# ---------------------------------------------------------------------------
# Page geometry (logical pixels @ 150 dpi → crisp A4)
# ---------------------------------------------------------------------------

A4_W = 1240
A4_H = 1754
MARGIN_X      = 72
MARGIN_Y      = 64
CONTENT_W     = A4_W - 2 * MARGIN_X    # 1096 px
BLOCK_GAP     = 38
ELEMENT_GAP   = 10
HEADING_H     = 56
MIN_CONTENT_H = 60


# ---------------------------------------------------------------------------
# Page canvas
# ---------------------------------------------------------------------------

class _PageCanvas:
    def __init__(self, bg: tuple, page_num: int = 1):
        self.img      = Image.new("RGB", (A4_W, A4_H), bg)
        self.draw     = ImageDraw.Draw(self.img)
        self.y        = MARGIN_Y
        self.bg       = bg
        self.page_num = page_num

    def remaining(self) -> int:
        return A4_H - MARGIN_Y - self.y

    def fits(self, height: int) -> bool:
        return self.remaining() >= height

    def advance(self, px: int) -> None:
        self.y += px

    def paste(self, img: Image.Image, gap_after: int = ELEMENT_GAP) -> None:
        """Paste *img* left-aligned at current Y within content margins."""
        x = MARGIN_X
        self.img.paste(img, (x, self.y))
        self.y += img.height + gap_after


# ---------------------------------------------------------------------------
# PDF assembler
# ---------------------------------------------------------------------------

class _PDFBuilder:
    def __init__(
        self,
        theme: str,
        show_code: bool,
        author_name: Optional[str],
        author_id: Optional[str],
        no_cover: bool,
        font_path: Optional[str],
        script_name: str,
        script_path: Optional[str | Path] = None,
    ):
        self.theme       = theme
        self.show_code   = show_code
        self.author_name = author_name
        self.author_id   = author_id
        self.no_cover    = no_cover
        self.font_path   = font_path
        self.script_name = script_name
        self.script_path = script_path

        self.bg = _PAGE_BG_DARK if theme == "dark" else _PAGE_BG_LIGHT
        self.heading_fg  = (235, 235, 235) if theme == "dark" else (20,  20,  20)
        self.heading_sub = (155, 155, 155) if theme == "dark" else (110, 110, 110)
        self.rule_col    = _ACCENT

        # Use the custom font for headings too if provided, else system bold
        self.heading_font   = _load_font(28, bold=True, custom_path=font_path)
        self.page_num_font  = _load_font(18, custom_path=font_path)

        self.pages: list[Image.Image] = []
        self._current: _PageCanvas = _PageCanvas(self.bg, page_num=1)

    # ── page management ────────────────────────────────────────────────

    def _finish_page(self) -> None:
        pn    = len(self.pages) + 1
        d     = ImageDraw.Draw(self._current.img)
        text  = str(pn)
        tw, th = _measure(d, text, self.page_num_font)
        d.text(
            ((A4_W - tw) // 2, A4_H - MARGIN_Y // 2 - th // 2),
            text, font=self.page_num_font, fill=self.heading_sub,
        )
        self.pages.append(self._current.img)

    def _new_page(self) -> None:
        self._finish_page()
        self._current = _PageCanvas(self.bg, page_num=len(self.pages) + 1)

    # ── heading ────────────────────────────────────────────────────────

    def _draw_heading(self, title: str) -> None:
        draw = self._current.draw
        font = self.heading_font

        # Truncate if wider than content
        display = title
        tw, th  = _measure(draw, display, font)
        while tw > CONTENT_W - 4 and len(display) > 10:
            display = display[:-4] + "…"
            tw, th  = _measure(draw, display, font)

        y = self._current.y
        draw.text((MARGIN_X, y), display, font=font, fill=self.heading_fg)
        y += th + 6
        # Blue accent underline (Word H2 style)
        draw.line([(MARGIN_X, y), (MARGIN_X + CONTENT_W, y)],
                  fill=self.rule_col, width=2)
        self._current.y = y + 10

    # ── image helpers ──────────────────────────────────────────────────

    def _fit_to_width(self, img: Image.Image) -> Image.Image:
        if img.width <= CONTENT_W:
            return img
        ratio = CONTENT_W / img.width
        return img.resize((CONTENT_W, int(img.height * ratio)), Image.LANCZOS)

    # ── block renderer ─────────────────────────────────────────────────

    def _add_block(self, result) -> None:
        """Add one BlockResult to the PDF stream, packing onto current page."""
        code_img: Optional[Image.Image] = None
        term_img: Optional[Image.Image] = None
        skip_img: Optional[Image.Image] = None
        fig_imgs: list[Image.Image] = []

        if result.skipped:
            skip_img = self._fit_to_width(render_terminal_output(
                stdout="", stderr="[Block skipped via # pyblock: skip]",
                width_px=CONTENT_W,
                font_path=self.font_path,
                show_prompt=False,
            ))
        else:
            if self.show_code and result.code.strip():
                code_img = self._fit_to_width(render_code_box(
                    result.code,
                    theme=self.theme,
                    width_px=CONTENT_W,
                    font_path=self.font_path,
                ))

            # Only render terminal output if there actually is text output
            if result.stdout.strip() or result.stderr.strip():
                term_img = self._fit_to_width(render_terminal_output(
                    stdout=result.stdout,
                    stderr=result.stderr,
                    width_px=CONTENT_W,
                    font_path=self.font_path,
                    show_prompt=True,
                    script_path=self.script_path,
                    script_name=self.script_name,
                    duration_ms=result.duration_ms,
                ))

            for fig in result.figures:
                fig_imgs.append(self._fit_to_width(fig))

        # Determine the first visual element to avoid orphan headings
        first_el = code_img or term_img or (fig_imgs[0] if fig_imgs else skip_img)
        if first_el is not None:
            max_single_page_h = A4_H - 2 * MARGIN_Y - HEADING_H - ELEMENT_GAP
            needed_h = HEADING_H + min(first_el.height, max_single_page_h) + ELEMENT_GAP
        else:
            needed_h = HEADING_H + MIN_CONTENT_H

        if not self._current.fits(needed_h) and self._current.y > MARGIN_Y:
            self._new_page()

        # Heading: drawn ONCE only
        self._draw_heading(result.title)

        # Code box
        if code_img is not None:
            if not self._current.fits(code_img.height + ELEMENT_GAP):
                self._new_page()
            self._current.paste(code_img, gap_after=ELEMENT_GAP)

        # Terminal output (if any)
        if term_img is not None:
            if not self._current.fits(term_img.height + ELEMENT_GAP):
                self._new_page()
            self._current.paste(term_img, gap_after=ELEMENT_GAP)

        if skip_img is not None:
            self._current.paste(skip_img, gap_after=ELEMENT_GAP)

        # Plots
        for fig_img in fig_imgs:
            if not self._current.fits(fig_img.height + ELEMENT_GAP):
                self._new_page()
            self._current.paste(fig_img, gap_after=ELEMENT_GAP)

        # Inter-block breathing room
        self._current.advance(BLOCK_GAP - ELEMENT_GAP)

    # ── public build ───────────────────────────────────────────────────

    def build(self, results: list) -> list[Image.Image]:
        total_ms = sum(r.duration_ms for r in results if not r.skipped)
        run_date = datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

        # Optional cover page
        if not self.no_cover:
            cover_page = render_cover_page(
                script_name=self.script_name,
                total_blocks=len([r for r in results if not r.skipped]),
                run_date=run_date,
                total_duration_ms=total_ms,
                theme=self.theme,
                author_name=self.author_name,
                author_id=self.author_id,
                width_px=A4_W,
                height_px=A4_H,
                font_path=self.font_path,
            )
            self.pages.append(cover_page)

        # Content pages
        self._current = _PageCanvas(self.bg, page_num=len(self.pages) + 1)
        for result in results:
            self._add_block(result)

        # Final page: combined terminal screenshot (only when there are multiple blocks with output)
        blocks_with_output = [
            r for r in results
            if not r.skipped and (r.stdout.strip() or r.stderr.strip())
        ]

        if len(blocks_with_output) > 1:
            combined_stdout = ""
            combined_stderr = ""
            for r in results:
                if not r.skipped:
                    if r.stdout:
                        combined_stdout += r.stdout
                    if r.stderr:
                        combined_stderr += r.stderr

            if combined_stdout.strip() or combined_stderr.strip():
                # Start on a fresh page for the combined output
                if self._current.y > MARGIN_Y:
                    self._new_page()

                self._draw_heading("All Tasks Combined Output")

                combined_term = render_terminal_output(
                    stdout=combined_stdout,
                    stderr=combined_stderr,
                    width_px=CONTENT_W,
                    font_path=self.font_path,
                    show_prompt=True,
                    script_path=self.script_path,
                    script_name=self.script_name,
                    duration_ms=total_ms,
                )
                combined_term = self._fit_to_width(combined_term)
                self._current.paste(combined_term, gap_after=ELEMENT_GAP)

        # Flush last page
        if self._current.y > MARGIN_Y:
            self._finish_page()

        return self.pages


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_pdf(
    results: list,
    output_path: str | Path,
    script_path: Optional[str | Path] = None,
    script_name: str = "script.py",
    theme: str = "light",
    show_code: bool = False,
    author_name: Optional[str] = None,
    author_id: Optional[str] = None,
    no_cover: bool = True,
    font_path: Optional[str] = None,
) -> Path:
    """
    Build a Word-doc style multi-page PDF.

    Parameters
    ----------
    script_path:
        Path to the target Python script (used for Starship directory & file display).
    no_cover:
        If True, skip the cover / title page entirely.
    font_path:
        Optional path to a TTF/OTF font file used for terminal output
        and code boxes (e.g. Iosevka NF). Falls back to bundled fonts.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if script_path and script_name == "script.py":
        script_name = Path(script_path).name

    builder = _PDFBuilder(
        theme=theme,
        show_code=show_code,
        author_name=author_name,
        author_id=author_id,
        no_cover=no_cover,
        font_path=font_path,
        script_name=script_name,
        script_path=script_path,
    )
    pages = builder.build(results)

    rgb_pages = [p.convert("RGB") for p in pages]
    if not rgb_pages:
        Image.new("RGB", (A4_W, A4_H)).save(str(output_path), "PDF")
        return output_path.resolve()

    rgb_pages[0].save(
        str(output_path),
        "PDF",
        save_all=True,
        append_images=rgb_pages[1:],
        resolution=150.0,
    )
    return output_path.resolve()
