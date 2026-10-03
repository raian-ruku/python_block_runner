"""
renderer.py - Output Renderer for PyBlockRunner

Produces:
  1. render_terminal_output() — a terminal screenshot matching a modern
     Starship (muted_glass palette) + Iosevka NF dark terminal style:
     - Pure black background (0, 0, 0)
     - Starship-styled top prompt line with file directory, Python version, OS icon, username
     - Real stdout & stderr lines matching terminal display
     - Starship-styled bottom prompt line with execution duration & cursor block
  2. render_code_box() — clean code snippet box with line numbers.
  3. render_cover_page() — clean A4 cover page.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .starship import StarshipRenderer


# ---------------------------------------------------------------------------
# Font discovery (prefers Iosevka NF)
# ---------------------------------------------------------------------------

def resolve_font_path(name_or_path: Optional[str] = None) -> Optional[str]:
    """
    Resolve a font name or file path to a usable TTF/OTF file path.
    If name_or_path is None, defaults to checking for Iosevka Nerd Font.
    """
    search_dirs: list[Path] = [
        Path.home() / "Library/Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path("C:/Windows/Fonts"),
    ]

    if not name_or_path:
        # Check for Iosevka Nerd Font by default
        for font_dir in search_dirs:
            if not font_dir.exists():
                continue
            for cand in [
                font_dir / "IosevkaNerdFont-Regular.ttf",
                font_dir / "IosevkaNerdFontMono-Regular.ttf",
                font_dir / "Iosevka-Regular.ttc",
            ]:
                if cand.exists():
                    return str(cand.resolve())
        return None

    # Direct path provided
    p = Path(name_or_path).expanduser()
    if p.exists() and p.suffix.lower() in ('.ttf', '.otf', '.ttc'):
        return str(p.resolve())

    # Fuzzy name search
    needle = name_or_path.lower().replace(" ", "").replace("-", "").replace("_", "")
    regular_match: Optional[str] = None
    any_match: Optional[str] = None

    for font_dir in search_dirs:
        if not font_dir.exists():
            continue
        for f in font_dir.rglob("*"):
            if f.suffix.lower() not in ('.ttf', '.otf', '.ttc'):
                continue
            fname = f.name.lower().replace(" ", "").replace("-", "").replace("_", "")
            if needle in fname:
                if any_match is None:
                    any_match = str(f)
                if "regular" in fname and regular_match is None:
                    regular_match = str(f)

    return regular_match or any_match


_FONT_CACHE: dict[tuple, ImageFont.ImageFont] = {}


def _load_font(
    size: int,
    bold: bool = False,
    custom_path: Optional[str] = None,
) -> ImageFont.ImageFont:
    """Load font at size px, preferring custom_path or Iosevka NF."""
    if not custom_path:
        custom_path = resolve_font_path(None)

    key = (size, bold, custom_path)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    if custom_path:
        paths_to_try: list[str] = []
        if bold:
            cp = Path(custom_path)
            for variant in ('Bold', 'SemiBold', 'Medium'):
                candidate = cp.parent / cp.name.replace('Regular', variant).replace('regular', variant)
                if candidate.exists():
                    paths_to_try.append(str(candidate))
        paths_to_try.append(custom_path)

        for path in paths_to_try:
            try:
                font = ImageFont.truetype(path, size)
                _FONT_CACHE[key] = font
                return font
            except (IOError, OSError):
                continue

    # System fallback chain (dynamically detects user home directory)
    fallback_chain = [
        str(Path.home() / "Library/Fonts/IosevkaNerdFont-Regular.ttf"),
        str(Path.home() / "Library/Fonts/IosevkaNerdFontMono-Regular.ttf"),
        str(Path.home() / ".local/share/fonts/IosevkaNerdFont-Regular.ttf"),
        "/Library/Fonts/IosevkaNerdFont-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/Library/Fonts/Courier New.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "C:\\Windows\\Fonts\\consola.ttf",
    ]
    for path in fallback_chain:
        try:
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[key] = font
            return font
        except (IOError, OSError):
            continue

    fallback = ImageFont.load_default()
    _FONT_CACHE[key] = fallback
    return fallback


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_lines(text: str, chars_per_line: int) -> list[str]:
    result: list[str] = []
    for raw in _strip_ansi(text).expandtabs(4).splitlines():
        wrapped = textwrap.wrap(raw, width=chars_per_line) or [""]
        result.extend(wrapped)
    return result


# ---------------------------------------------------------------------------
# Terminal colours (pure black terminal matching user screenshot)
# ---------------------------------------------------------------------------

_TERM_BG       = (0, 0, 0)         # pitch black
_TERM_FG       = (235, 235, 235)   # bright off-white
_TERM_ERR      = (255, 85, 85)     # red stderr
_TERM_STUB     = (180, 180, 90)    # intercepted note
_TERM_EMPTY_FG = (110, 110, 110)   # (no output) dim


# ---------------------------------------------------------------------------
# Terminal Screenshot Renderer
# ---------------------------------------------------------------------------

def _render_default_prompt_bar(
    font: ImageFont.ImageFont,
    script_path: Optional[str | Path],
    script_name: str,
    duration_ms: float = 0.0,
    is_bottom: bool = False,
    width_px: int = 1000,
) -> Image.Image:
    """Render a clean, authentic standard Unix/macOS terminal prompt bar (default style)."""
    from .starship import get_current_username, get_display_directory
    import platform
    dummy = Image.new("RGB", (1, 1))
    d_dummy = ImageDraw.Draw(dummy)

    username = get_current_username()
    host = "MacBook-Pro" if platform.system() == "Darwin" else "localhost"
    dir_str = get_display_directory(script_path)
    sym = "%" if platform.system() == "Darwin" else "$"

    char_w, char_h = _measure(d_dummy, "M", font)
    bar_h = char_h + 10
    img = Image.new("RGB", (width_px, bar_h), _TERM_BG)
    draw = ImageDraw.Draw(img)

    y = 5
    x = 12
    # Draw user@host in green
    user_host = f"{username}@{host}"
    draw.text((x, y), user_host, font=font, fill=(90, 247, 142))
    x += _measure(d_dummy, user_host, font)[0]
    draw.text((x, y), ":", font=font, fill=(200, 200, 200))
    x += _measure(d_dummy, ":", font)[0]

    # Draw directory in cyan/blue
    draw.text((x, y), dir_str, font=font, fill=(87, 199, 255))
    x += _measure(d_dummy, dir_str + " ", font)[0]

    # Draw prompt symbol
    draw.text((x, y), sym, font=font, fill=(235, 235, 235))
    x += _measure(d_dummy, sym + " ", font)[0]

    if not is_bottom:
        cmd = f"python3 {script_name}"
        draw.text((x, y), cmd, font=font, fill=(255, 255, 255))
    else:
        cursor_w = max(8, char_w)
        draw.rectangle([(x, y + 2), (x + cursor_w, y + char_h + 2)], fill=(235, 235, 235))

    return img


def render_terminal_output(
    stdout: str,
    stderr: str,
    width_px: int = 1000,
    max_lines: int = 120,
    font_size: int = 20,
    font_path: Optional[str] = None,
    show_prompt: bool = True,
    script_path: Optional[str | Path] = None,
    script_name: str = "script.py",
    duration_ms: float = 0.0,
    prompt_style: str = "auto",
) -> Image.Image:
    """
    Render stdout + stderr as a terminal screenshot:
      - Pitch black background
      - Authentic prompt bar at top and bottom (default terminal or Starship)
      - Clean monospace text in between
    """
    # Terminal screenshots ALWAYS use Iosevka Nerd Font
    term_font_path = resolve_font_path("Iosevka NF") or resolve_font_path(None)
    font = _load_font(font_size, custom_path=term_font_path)

    has_starship_config = (Path.home() / ".config" / "starship.toml").exists()
    use_starship = (prompt_style == "starship") or (prompt_style == "auto" and has_starship_config)

    # Calculate required width based on prompt bar & text lines
    actual_width = width_px
    sr = None
    if show_prompt:
        if use_starship:
            sr = StarshipRenderer(font)
            prompt_w = sr.calculate_required_width(script_path, script_name, duration_ms)
            actual_width = max(actual_width, prompt_w)
        else:
            dummy = Image.new("RGB", (1, 1))
            d_dummy = ImageDraw.Draw(dummy)
            from .starship import get_current_username, get_display_directory
            def_str = f"{get_current_username()}@MacBook-Pro:{get_display_directory(script_path)} % python3 {script_name}"
            actual_width = max(actual_width, _measure(d_dummy, def_str, font)[0] + 60)

    dummy = Image.new("RGB", (1, 1))
    d_dummy = ImageDraw.Draw(dummy)
    char_w, char_h = _measure(d_dummy, "M", font)
    pad_x = 12
    pad_y = 8
    chars_per_line = max(40, (actual_width - pad_x * 2) // max(1, char_w))
    line_h = char_h + 6

    # Content lines
    lines: list[tuple[str, tuple]] = []

    if stdout and stdout.strip():
        for ln in _wrap_lines(stdout.rstrip(), chars_per_line):
            lines.append((ln, _TERM_FG))

    if stderr and stderr.strip():
        if lines:
            lines.append(("", _TERM_FG))
        for ln in _wrap_lines(stderr.rstrip(), chars_per_line):
            lines.append((ln, _TERM_ERR))

    if len(lines) > max_lines:
        cut = len(lines) - max_lines
        lines = lines[:max_lines]
        lines.append((f"  … {cut} more lines truncated", _TERM_STUB))

    if not lines:
        lines = [("(no output)", _TERM_EMPTY_FG)]

    content_h = pad_y + line_h * len(lines) + pad_y

    top_bar = None
    bot_bar = None
    if show_prompt:
        if use_starship and sr is not None:
            top_bar = sr.render_prompt_bar(
                script_path=script_path,
                script_name=script_name,
                duration_ms=duration_ms,
                is_bottom=False,
                width_px=actual_width,
            )
            bot_bar = sr.render_prompt_bar(
                script_path=script_path,
                script_name=script_name,
                duration_ms=duration_ms,
                is_bottom=True,
                width_px=actual_width,
            )
        else:
            top_bar = _render_default_prompt_bar(
                font=font,
                script_path=script_path,
                script_name=script_name,
                duration_ms=duration_ms,
                is_bottom=False,
                width_px=actual_width,
            )
            bot_bar = _render_default_prompt_bar(
                font=font,
                script_path=script_path,
                script_name=script_name,
                duration_ms=duration_ms,
                is_bottom=True,
                width_px=actual_width,
            )
        actual_width = max(actual_width, top_bar.width, bot_bar.width)
        total_h = top_bar.height + content_h + bot_bar.height
    else:
        total_h = content_h

    img = Image.new("RGB", (actual_width, total_h), _TERM_BG)
    draw = ImageDraw.Draw(img)

    content_y0 = 0
    if top_bar:
        img.paste(top_bar, (0, 0))
        content_y0 = top_bar.height

    y = content_y0 + pad_y
    for text, color in lines:
        if text:
            draw.text((pad_x, y), text, font=font, fill=color)
        y += line_h

    if bot_bar:
        img.paste(bot_bar, (0, content_y0 + content_h))

    return img


# ---------------------------------------------------------------------------
# Code Box
# ---------------------------------------------------------------------------

_CODE_BG_DARK  = (28, 28, 28)
_CODE_FG_DARK  = (180, 215, 175)
_CODE_LN_DARK  = (95, 95, 95)
_CODE_BG_LIGHT = (245, 245, 245)
_CODE_FG_LIGHT = (40, 90, 40)
_CODE_LN_LIGHT = (170, 170, 170)


def render_code_box(
    code: str,
    theme: str = "dark",
    width_px: int = 1000,
    font_size: int = 19,
    font_path: Optional[str] = None,
) -> Image.Image:
    """Plain code snippet box with line numbers."""
    if theme == "light":
        bg, fg, ln_col = _CODE_BG_LIGHT, _CODE_FG_LIGHT, _CODE_LN_LIGHT
    else:
        bg, fg, ln_col = _CODE_BG_DARK, _CODE_FG_DARK, _CODE_LN_DARK

    font = _load_font(font_size, custom_path=font_path)
    dummy = Image.new("RGB", (1, 1))
    d_dummy = ImageDraw.Draw(dummy)
    char_w, char_h = _measure(d_dummy, "M", font)
    pad_x, pad_y = 16, 10
    ln_w = 40
    chars_per_line = max(40, (width_px - pad_x * 2 - ln_w) // max(1, char_w))
    line_h = char_h + 5

    source_lines: list[str] = []
    for raw in _strip_ansi(code).expandtabs(4).splitlines():
        wrapped = textwrap.wrap(raw, width=chars_per_line) or [""]
        source_lines.extend(wrapped)

    img_h = pad_y * 2 + line_h * len(source_lines)
    img = Image.new("RGB", (width_px, img_h), bg)
    draw = ImageDraw.Draw(img)

    y = pad_y
    for i, text in enumerate(source_lines, start=1):
        draw.text((pad_x, y), str(i), font=font, fill=ln_col)
        draw.text((pad_x + ln_w, y), text, font=font, fill=fg)
        y += line_h

    return img


# ---------------------------------------------------------------------------
# Cover Page (Redesigned: ONLY Name & ID with Elegant Architectural Styling)
# ---------------------------------------------------------------------------

_PAGE_BG_DARK  = (11, 15, 25)
_PAGE_BG_LIGHT = (252, 252, 253)
_ACCENT        = (79, 140, 201)


def render_cover_page(
    script_name: str = "",
    total_blocks: int = 0,
    run_date: str = "",
    total_duration_ms: float = 0.0,
    theme: str = "light",
    author_name: Optional[str] = None,
    author_id: Optional[str] = None,
    width_px: int = 1240,
    height_px: int = 1754,
    font_path: Optional[str] = None,
) -> Image.Image:
    """
    Render a formal, elegant cover page featuring ONLY the student's Name and ID,
    adorned with precision architectural framing, geometric corner crosshairs,
    an ornamental crest, and a floating credential showcase card.
    """
    W, H = width_px, height_px

    if theme == "dark":
        bg            = (11, 15, 25)        # Deep midnight
        accent        = (56, 189, 248)      # Sky blue (#38bdf8)
        border_subtle = (30, 41, 59)        # Slate-800
        card_bg       = (20, 27, 45)        # Card surface
        card_border   = (51, 65, 85)        # Slate-700
        name_fg       = (248, 250, 252)     # Crisp white
        label_fg      = (56, 189, 248)      # Accent sky blue
        id_badge_bg   = (15, 23, 42)        # Slate-900 badge
        id_badge_fg   = (248, 250, 252)     # White ID text
    else:
        bg            = (252, 252, 253)     # Porcelain white
        accent        = (79, 140, 201)      # Sapphire accent (#4f8cc9)
        border_subtle = (226, 232, 240)     # Slate-200
        card_bg       = (255, 255, 255)     # Pure white card
        card_border   = (203, 213, 225)     # Slate-300
        name_fg       = (15, 23, 42)        # Dark navy/slate-900
        label_fg      = (79, 140, 201)      # Sapphire accent
        id_badge_bg   = (248, 250, 252)     # Light slate badge
        id_badge_fg   = (15, 23, 42)        # Dark slate ID text

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    # 1. Outer Architectural Double-Border & Corner Crosshairs (on full page)
    if H >= 900:
        inset = 56
        draw.rectangle([(inset, inset), (W - inset, H - inset)], outline=accent, width=2)
        inner_inset = inset + 12
        draw.rectangle([(inner_inset, inner_inset), (W - inner_inset, H - inner_inset)], outline=border_subtle, width=1)

        # Precision corner bracket crosshairs (L-shapes)
        corner_len = 36
        c_off = 40
        # Top-Left
        draw.line([(c_off, c_off), (c_off + corner_len, c_off)], fill=accent, width=3)
        draw.line([(c_off, c_off), (c_off, c_off + corner_len)], fill=accent, width=3)
        # Top-Right
        draw.line([(W - c_off, c_off), (W - c_off - corner_len, c_off)], fill=accent, width=3)
        draw.line([(W - c_off, c_off), (W - c_off, c_off + corner_len)], fill=accent, width=3)
        # Bottom-Left
        draw.line([(c_off, H - c_off), (c_off + corner_len, H - c_off)], fill=accent, width=3)
        draw.line([(c_off, H - c_off), (c_off, H - c_off - corner_len)], fill=accent, width=3)
        # Bottom-Right
        draw.line([(W - c_off, H - c_off), (W - c_off - corner_len, H - c_off)], fill=accent, width=3)
        draw.line([(W - c_off, H - c_off), (W - c_off, H - c_off - corner_len)], fill=accent, width=3)

        # Top Header Symmetrical Geometric Decoration
        top_y = 140
        cx = W // 2
        draw.polygon([(cx, top_y - 8), (cx + 8, top_y), (cx, top_y + 8), (cx - 8, top_y)], fill=accent)
        draw.line([(220, top_y), (cx - 24, top_y)], fill=accent, width=1)
        draw.line([(cx + 24, top_y), (W - 220, top_y)], fill=accent, width=1)
        draw.line([(320, top_y + 6), (cx - 40, top_y + 6)], fill=border_subtle, width=1)
        draw.line([(cx + 40, top_y + 6), (W - 320, top_y + 6)], fill=border_subtle, width=1)

        # Bottom Footer Symmetrical Geometric Decoration
        bot_y = H - 140
        draw.polygon([(cx, bot_y - 8), (cx + 8, bot_y), (cx, bot_y + 8), (cx - 8, bot_y)], fill=accent)
        draw.line([(220, bot_y), (cx - 24, bot_y)], fill=accent, width=1)
        draw.line([(cx + 24, bot_y), (W - 220, bot_y)], fill=accent, width=1)
        draw.line([(320, bot_y - 6), (cx - 40, bot_y - 6)], fill=border_subtle, width=1)
        draw.line([(cx + 40, bot_y - 6), (W - 320, bot_y - 6)], fill=border_subtle, width=1)
    else:
        cx = W // 2

    # 2. Central Featured Credential Showcase Card (Name & ID)
    card_w = min(880, W - 120)
    card_h = min(660, H - 120) if H < 900 else 660
    card_x0 = (W - card_w) // 2
    card_x1 = card_x0 + card_w
    card_y0 = (H - card_h) // 2
    card_y1 = card_y0 + card_h

    # Card outer halo / soft shadow
    draw.rounded_rectangle([(card_x0 - 4, card_y0 - 4), (card_x1 + 4, card_y1 + 4)], radius=22, outline=border_subtle, width=2)
    # Card body
    draw.rounded_rectangle([(card_x0, card_y0), (card_x1, card_y1)], radius=18, fill=card_bg, outline=card_border, width=2)
    # Inner border frame on card
    draw.rounded_rectangle([(card_x0 + 16, card_y0 + 16), (card_x1 - 16, card_y1 - 16)], radius=12, outline=border_subtle, width=1)

    # Decorative corner L-notches inside card
    notch_s = 14
    # Top-Left card notch
    draw.line([(card_x0 + 26, card_y0 + 26), (card_x0 + 26 + notch_s, card_y0 + 26)], fill=accent, width=1)
    draw.line([(card_x0 + 26, card_y0 + 26), (card_x0 + 26, card_y0 + 26 + notch_s)], fill=accent, width=1)
    # Top-Right card notch
    draw.line([(card_x1 - 26, card_y0 + 26), (card_x1 - 26 - notch_s, card_y0 + 26)], fill=accent, width=1)
    draw.line([(card_x1 - 26, card_y0 + 26), (card_x1 - 26, card_y0 + 26 + notch_s)], fill=accent, width=1)
    # Bottom-Left card notch
    draw.line([(card_x0 + 26, card_y1 - 26), (card_x0 + 26 + notch_s, card_y1 - 26)], fill=accent, width=1)
    draw.line([(card_x0 + 26, card_y1 - 26), (card_x0 + 26, card_y1 - 26 - notch_s)], fill=accent, width=1)
    # Bottom-Right card notch
    draw.line([(card_x1 - 26, card_y1 - 26), (card_x1 - 26 - notch_s, card_y1 - 26)], fill=accent, width=1)
    draw.line([(card_x1 - 26, card_y1 - 26), (card_x1 - 26, card_y1 - 26 - notch_s)], fill=accent, width=1)

    # 3. Geometric Crest / Seal at Top of Card
    crest_cy = card_y0 + 72
    # Outer diamond
    draw.polygon([(cx, crest_cy - 26), (cx + 26, crest_cy), (cx, crest_cy + 26), (cx - 26, crest_cy)], outline=accent, width=2)
    # Inner diamond
    draw.polygon([(cx, crest_cy - 14), (cx + 14, crest_cy), (cx, crest_cy + 14), (cx - 14, crest_cy)], fill=accent)
    # Wings
    draw.line([(card_x0 + 120, crest_cy), (cx - 45, crest_cy)], fill=card_border, width=1)
    draw.line([(cx + 45, crest_cy), (card_x1 - 120, crest_cy)], fill=card_border, width=1)
    draw.ellipse([(card_x0 + 116, crest_cy - 2), (card_x0 + 120, crest_cy + 2)], fill=accent)
    draw.ellipse([(card_x1 - 120, crest_cy - 2), (card_x1 - 116, crest_cy + 2)], fill=accent)

    # 4. Fonts
    label_font = _load_font(20, bold=True, custom_path=font_path)
    name_font  = _load_font(48, bold=True, custom_path=font_path)
    id_font    = _load_font(34, bold=True, custom_path=font_path)

    # 5. Section 1: STUDENT NAME
    display_name = (author_name or "").strip() or "—"
    t_label1 = "S T U D E N T   N A M E"
    lw1, lh1 = _measure(draw, t_label1, label_font)
    nw, nh = _measure(draw, display_name, name_font)

    y_label1 = card_y0 + 138
    draw.text(((W - lw1) // 2, y_label1), t_label1, font=label_font, fill=label_fg)

    y_name = card_y0 + 178
    draw.text(((W - nw) // 2, y_name), display_name, font=name_font, fill=name_fg)

    # 6. Center Symmetrical Ornamental Divider
    div_y = card_y0 + 288
    draw.line([(card_x0 + 140, div_y), (cx - 50, div_y)], fill=card_border, width=1)
    draw.line([(cx + 50, div_y), (card_x1 - 140, div_y)], fill=card_border, width=1)
    # Center diamond and flank dots
    draw.polygon([(cx, div_y - 8), (cx + 8, div_y), (cx, div_y + 8), (cx - 8, div_y)], fill=accent)
    draw.ellipse([(cx - 28, div_y - 2), (cx - 24, div_y + 2)], fill=accent)
    draw.ellipse([(cx + 24, div_y - 2), (cx + 28, div_y + 2)], fill=accent)

    # 7. Section 2: STUDENT ID
    display_id = (author_id or "").strip() or "—"
    t_label2 = "S T U D E N T   I D"
    lw2, lh2 = _measure(draw, t_label2, label_font)
    iw, ih = _measure(draw, display_id, id_font)

    y_label2 = card_y0 + 348
    draw.text(((W - lw2) // 2, y_label2), t_label2, font=label_font, fill=label_fg)

    # Credential pill badge
    badge_pad_x = 48
    badge_w = max(340, min(iw + badge_pad_x * 2, card_w - 160))
    badge_h = 66
    badge_x0 = (W - badge_w) // 2
    badge_x1 = badge_x0 + badge_w
    badge_y0 = card_y0 + 396
    badge_y1 = badge_y0 + badge_h

    draw.rounded_rectangle([(badge_x0, badge_y0), (badge_x1, badge_y1)], radius=14, fill=id_badge_bg, outline=accent, width=2)
    draw.text(((W - iw) // 2, badge_y0 + (badge_h - ih) // 2), display_id, font=id_font, fill=id_badge_fg)

    # 8. Bottom Card Decorative Line
    bot_card_y = card_y0 + 540
    draw.line([(card_x0 + 200, bot_card_y), (card_x1 - 200, bot_card_y)], fill=card_border, width=1)
    draw.polygon([(cx, bot_card_y - 5), (cx + 5, bot_card_y), (cx, bot_card_y + 5), (cx - 5, bot_card_y)], fill=accent)
    draw.ellipse([(card_x0 + 196, bot_card_y - 2), (card_x0 + 200, bot_card_y + 2)], fill=accent)
    draw.ellipse([(card_x1 - 200, bot_card_y - 2), (card_x1 - 196, bot_card_y + 2)], fill=accent)

    return img
