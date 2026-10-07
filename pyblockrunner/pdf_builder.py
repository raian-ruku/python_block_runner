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

    def paste(self, img: Image.Image, gap_after: int = ELEMENT_GAP, center: bool = False) -> None:
        """Paste *img* left-aligned or centered at current Y within content margins."""
        if center and img.width < CONTENT_W:
            x = MARGIN_X + (CONTENT_W - img.width) // 2
        else:
            x = MARGIN_X
        self.img.paste(img, (x, self.y))
        self.y += img.height + gap_after


# ---------------------------------------------------------------------------
# Heading wrapping helper
# ---------------------------------------------------------------------------

def _wrap_heading_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Wrap heading text cleanly into lines so that no characters or words are truncated."""
    clean_text = text.strip()
    if not clean_text:
        return []
    lines: list[str] = []
    for paragraph in clean_text.splitlines():
        words = paragraph.split()
        if not words:
            continue
        cur_line = ""
        for w in words:
            cand = f"{cur_line} {w}" if cur_line else w
            cw, _ = _measure(draw, cand, font)
            if cw <= max_width:
                cur_line = cand
            else:
                if cur_line:
                    lines.append(cur_line)
                ww, _ = _measure(draw, w, font)
                if ww > max_width:
                    chunk = ""
                    for ch in w:
                        if _measure(draw, chunk + ch, font)[0] <= max_width:
                            chunk += ch
                        else:
                            if chunk:
                                lines.append(chunk)
                            chunk = ch
                    cur_line = chunk
                else:
                    cur_line = w
        if cur_line:
            lines.append(cur_line)
    return lines or [clean_text]


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
        self._pending_headers: list[str] = []
        self._page_has_visual_content: bool = False

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
        self._page_has_visual_content = False

    # ── heading ────────────────────────────────────────────────────────

    def _measure_heading_height(self, title: str, is_section: bool = False) -> int:
        lines = _wrap_heading_text(self._current.draw, title, self.heading_font, CONTENT_W - 4)
        if not lines:
            return HEADING_H
        total_h = 0
        for line in lines:
            _, th = _measure(self._current.draw, line, self.heading_font)
            total_h += th + 6
        total_h += 6 + (14 if is_section else 10)
        return max(HEADING_H, total_h)

    def _draw_heading(self, title: str, is_section: bool = False) -> None:
        draw = self._current.draw
        font = self.heading_font
        lines = _wrap_heading_text(draw, title, font, CONTENT_W - 4)
        if not lines:
            lines = [title]

        y = self._current.y
        for line in lines:
            tw, th = _measure(draw, line, font)
            draw.text((MARGIN_X, y), line, font=font, fill=self.heading_fg)
            y += th + 6

        y += 2
        # Blue accent underline (Word H2 style)
        line_w = 3 if is_section else 2
        draw.line([(MARGIN_X, y), (MARGIN_X + CONTENT_W, y)],
                  fill=self.rule_col, width=line_w)
        self._current.y = y + (14 if is_section else 10)

    # ── image helpers ──────────────────────────────────────────────────

    def _fit_to_width(self, img: Image.Image) -> Image.Image:
        if img.width <= CONTENT_W:
            return img
        ratio = CONTENT_W / img.width
        return img.resize((CONTENT_W, int(img.height * ratio)), Image.LANCZOS)

    def _paste_element(self, img: Image.Image, gap_after: int = ELEMENT_GAP) -> None:
        """
        Paste *img* into the document cleanly:
        - If img fits on current page: paste directly.
        - If img fits on a full single page, but doesn't fit on current page and current page already
          has visual content, move to a new page.
        - If img exceeds the full page height:
          * If moderately oversized (<= 1.35x remaining space): scale aspect ratio proportionally to fit.
          * If significantly oversized (> 1.35x remaining space): split into multiple page images.
        """
        max_page_h = A4_H - 2 * MARGIN_Y
        rem = self._current.remaining()

        # If it fits on current page: paste directly
        if img.height <= rem:
            self._current.paste(img, gap_after=gap_after)
            self._page_has_visual_content = True
            return

        # If it fits on a full single page, but doesn't fit in remaining space:
        # Move to a new page only if the current page already has prior visual content
        if img.height <= max_page_h and self._page_has_visual_content and self._current.y > MARGIN_Y + HEADING_H + 20:
            self._new_page()
            rem = self._current.remaining()
            if img.height <= rem:
                self._current.paste(img, gap_after=gap_after)
                self._page_has_visual_content = True
                return

        # Case 1: Moderately tall (<= 1.35x remaining space) -> scale aspect ratio proportionally to fit
        if img.height <= rem * 1.35 and rem >= 400:
            scale = rem / img.height
            new_w = max(100, int(img.width * scale))
            new_h = int(img.height * scale)
            scaled = img.resize((new_w, new_h), Image.LANCZOS)
            self._current.paste(scaled, gap_after=gap_after, center=True)
            self._page_has_visual_content = True
            return

        # Case 2: Significantly tall -> split across multiple pages as multiple images
        y_offset = 0
        total_h = img.height
        while y_offset < total_h:
            curr_rem = self._current.remaining()
            # If remaining space is too small to fit a meaningful slice, move to new page
            if curr_rem < 300:
                self._new_page()
                curr_rem = self._current.remaining()

            slice_h = min(curr_rem, total_h - y_offset)
            chunk = img.crop((0, y_offset, img.width, y_offset + slice_h))
            is_last = (y_offset + slice_h >= total_h)
            self._current.paste(chunk, gap_after=gap_after if is_last else 0)
            self._page_has_visual_content = True
            y_offset += slice_h
            if not is_last:
                self._new_page()

    # ── block renderer ─────────────────────────────────────────────────

    def _add_block(self, result) -> None:
        """Add one BlockResult to the PDF stream, packing onto current page."""
        code_img: Optional[Image.Image] = None
        term_img: Optional[Image.Image] = None
        skip_img: Optional[Image.Image] = None
        fig_imgs: list[Image.Image] = []

        if result.skipped:
            if not result.code.strip():
                # A header/prelude block that was skipped has no code and needs no skip box
                return
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

        # Check if this block has any visual output elements
        has_visual = (code_img is not None) or (term_img is not None) or (skip_img is not None) or bool(fig_imgs)

        if not has_visual:
            # Block has no visual output (pure section header, or code not shown with no output)
            # Defer drawing until the first visual content arrives to prevent orphan blank pages
            self._pending_headers.append(result.title)
            return

        first_el = code_img or term_img or (fig_imgs[0] if fig_imgs else skip_img)
        min_content_needed = min(first_el.height, 220)
        pending_h = sum(self._measure_heading_height(p_title, is_section=True) for p_title in self._pending_headers)
        block_heading_h = self._measure_heading_height(result.title, is_section=False)
        needed_h = pending_h + block_heading_h + min_content_needed + ELEMENT_GAP

        # If current page already has visual content and cannot fit headings + initial chunk, move to fresh page
        if self._page_has_visual_content and not self._current.fits(needed_h):
            self._new_page()

        # Draw pending section headers (e.g. "Part 1")
        for p_title in self._pending_headers:
            self._draw_heading(p_title, is_section=True)
        self._pending_headers.clear()

        # Draw current block heading
        self._draw_heading(result.title, is_section=False)

        # Code box
        if code_img is not None:
            self._paste_element(code_img, gap_after=ELEMENT_GAP)

        # Terminal output (if any)
        if term_img is not None:
            self._paste_element(term_img, gap_after=ELEMENT_GAP)

        if skip_img is not None:
            self._paste_element(skip_img, gap_after=ELEMENT_GAP)

        # Plots
        for fig_img in fig_imgs:
            self._paste_element(fig_img, gap_after=ELEMENT_GAP)

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
        self._page_has_visual_content = False
        for result in results:
            self._add_block(result)

        # Flush any trailing pending headers (e.g. if the last block had no output)
        if self._pending_headers:
            for p_title in self._pending_headers:
                self._draw_heading(p_title, is_section=True)
            self._pending_headers.clear()

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
                self._paste_element(combined_term, gap_after=ELEMENT_GAP)

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
