"""
starship.py - Starship Prompt Renderer for PyBlockRunner

Faithfully reproduces the user's Starship prompt (muted_glass palette)
and terminal style from ~/.config/starship.toml:

  [](fg:glass0) 󰀵  username [](fg:glass0 bg:glass1) ~/dir [](fg:glass1 bg:glass3)  v3.14.7 [](fg:glass3 bg:glass5)  03:04 PM [](fg:glass5) ❯ python3 script.py

Bottom bar:
  [](fg:glass0) 󰀵  username [](fg:glass0 bg:glass1) ~/dir [](fg:glass1 bg:glass3)  v3.14.7 [](fg:glass3 bg:glass5)  03:04 PM [](fg:glass5) 󰔛 22s48ms❯ █
"""

from __future__ import annotations

import datetime
import os
import re
import sys
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont


# Default palette from muted_glass
_DEFAULT_PALETTE = {
    "text":      (200, 206, 216),
    "text_dim":  (157, 165, 177),
    "glass0":    (59, 65, 74),
    "glass1":    (66, 72, 82),
    "glass2":    (74, 80, 90),
    "glass3":    (81, 88, 98),
    "glass4":    (89, 97, 108),
    "glass5":    (98, 106, 117),
}

_GLYPH_LEFT_CAP  = "\ue0b6"     # 
_GLYPH_CHEVRON   = "\ue0b0"     # 
_GLYPH_RIGHT_CAP = "\ue0b4"     # 
_GLYPH_APPLE     = "\U000f0035" # 󰀵 (Apple logo Nerd Font)
_GLYPH_GIT       = "\uf418"     #  (Git branch icon Nerd Font)
_GLYPH_PYTHON    = "\ue606"     #  (Python icon Nerd Font)
_GLYPH_CLOCK     = "\uf43a"     #  (Clock icon Nerd Font)
_GLYPH_TIMER     = "\U000f051b" # 󰔛 (Timer icon Nerd Font)
_GLYPH_PROMPT    = "❯"


def get_current_username() -> str:
    """Return the active system username dynamically without hardcoding."""
    try:
        import getpass
        u = getpass.getuser()
        if u:
            return u
    except Exception:
        pass
    for env in ("USER", "USERNAME", "LOGNAME"):
        val = os.environ.get(env)
        if val:
            return val
    return "user"


def get_git_branch(target_dir: Optional[Path] = None) -> Optional[str]:
    """Detect current git branch if the directory is inside a git repository."""
    try:
        curr = (target_dir or Path.cwd()).resolve()
        for parent in [curr] + list(curr.parents):
            git_head = parent / ".git" / "HEAD"
            if git_head.is_file():
                content = git_head.read_text(encoding="utf-8", errors="replace").strip()
                if content.startswith("ref: refs/heads/"):
                    return content.split("ref: refs/heads/")[-1]
                return content[:7]
    except Exception:
        pass
    return None


def get_os_glyph(has_nerd_font: bool = True) -> str:
    """Return OS-appropriate icon (Apple on macOS, Tux on Linux, Windows flag on Windows)."""
    import platform
    sys_name = platform.system()
    if sys_name == "Darwin":
        return _GLYPH_APPLE if has_nerd_font else ""
    elif sys_name == "Windows":
        return "\ue70f" if has_nerd_font else "⊞"
    else:
        return "\uf17c" if has_nerd_font else "$"


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def load_starship_palette() -> dict[str, tuple[int, int, int]]:
    """Read ~/.config/starship.toml and extract active palette colors."""
    palette = dict(_DEFAULT_PALETTE)
    config_file = Path.home() / ".config" / "starship.toml"
    if not config_file.exists():
        return palette

    try:
        content = config_file.read_text(encoding="utf-8", errors="replace")
        # Find active palette
        m = re.search(r'^\s*palette\s*=\s*"([^"]+)"', content, re.MULTILINE)
        palette_name = m.group(1) if m else "muted_glass"

        sec_re = re.compile(
            rf'\[palettes\.{re.escape(palette_name)}\](.*?)(?=^\[|\Z)',
            re.DOTALL | re.MULTILINE,
        )
        sm = sec_re.search(content)
        if sm:
            for line in sm.group(1).splitlines():
                kv = re.match(r'^\s*([a-zA-Z0-9_-]+)\s*=\s*"(#[0-9a-fA-F]+)"', line)
                if kv:
                    palette[kv.group(1)] = _hex_to_rgb(kv.group(2))
    except Exception:
        pass
    return palette


def format_duration(ms: float) -> str:
    """Format duration cleanly (e.g. 22s48ms, 120ms, 0ms, 1m12s)."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    s = int(ms // 1000)
    rem_ms = int(ms % 1000)
    if s >= 60:
        m = s // 60
        rem_s = s % 60
        return f"{m}m{rem_s}s"
    if rem_ms > 0:
        return f"{s}s{rem_ms}ms"
    return f"{s}s"


def get_display_directory(script_path: Optional[str | Path]) -> str:
    """Format the directory of the file with ~ prefix."""
    if not script_path:
        target_dir = Path.cwd()
    else:
        sp = Path(script_path).resolve()
        target_dir = sp.parent if (sp.is_file() or sp.suffix) else sp

    home = Path.home()
    try:
        rel = target_dir.relative_to(home)
        return f"~/{rel}"
    except ValueError:
        return str(target_dir)


def _center_y(y0: int, cap_h: int, bbox: tuple[int, int, int, int]) -> int:
    """Calculate vertical coordinate to mathematically center a glyph/text within cap_h."""
    return y0 + (cap_h - (bbox[1] + bbox[3])) // 2



class StarshipRenderer:
    def __init__(self, font: ImageFont.ImageFont):
        self.font = font
        self.palette = load_starship_palette()
        self.glass0 = self.palette.get("glass0", _DEFAULT_PALETTE["glass0"])
        self.glass1 = self.palette.get("glass1", _DEFAULT_PALETTE["glass1"])
        self.glass2 = self.palette.get("glass2", _DEFAULT_PALETTE["glass2"])
        self.glass3 = self.palette.get("glass3", _DEFAULT_PALETTE["glass3"])
        self.glass5 = self.palette.get("glass5", _DEFAULT_PALETTE["glass5"])
        self.text_col = self.palette.get("text", _DEFAULT_PALETTE["text"])
        self.dim_col = self.palette.get("text_dim", (157, 165, 177))

    def calculate_required_width(
        self,
        script_path: Optional[str | Path],
        script_name: str,
        duration_ms: float = 0.0,
    ) -> int:
        """Calculate the exact minimum width required to render the full prompt bar without clipping."""
        import platform
        font = self.font
        dummy = Image.new("RGB", (1, 1))
        d_dummy = ImageDraw.Draw(dummy)

        bb_cap = d_dummy.textbbox((0, 0), _GLYPH_LEFT_CAP, font=font)
        cap_w = bb_cap[2] - bb_cap[0]
        bb_chev = d_dummy.textbbox((0, 0), _GLYPH_CHEVRON, font=font)
        chev_w = bb_chev[2] - bb_chev[0]
        bb_end = d_dummy.textbbox((0, 0), _GLYPH_RIGHT_CAP, font=font)
        end_w = bb_end[2] - bb_end[0]

        username = get_current_username()
        os_glyph = get_os_glyph()
        dir_str = get_display_directory(script_path)
        py_ver = f"v{sys.version.split()[0]}"
        now = datetime.datetime.now().strftime("%I:%M %p").lstrip("0")
        if not now.startswith("1") and len(now) < 8:
            now = "0" + now

        t0 = f" {os_glyph}  {username} "
        t1 = f" {dir_str} "
        tb0 = d_dummy.textbbox((0, 0), t0, font=font)
        tb1 = d_dummy.textbbox((0, 0), t1, font=font)

        pill_w = cap_w + (tb0[2] - tb0[0]) + chev_w + (tb1[2] - tb1[0])

        sp = Path(script_path) if script_path else None
        git_branch = get_git_branch(sp.parent if sp and (sp.is_file() or sp.suffix) else sp)
        if git_branch:
            t_git = f" {_GLYPH_GIT} {git_branch} "
            tb_git = d_dummy.textbbox((0, 0), t_git, font=font)
            pill_w += chev_w + (tb_git[2] - tb_git[0])

        t2 = f" {_GLYPH_PYTHON} {py_ver} "
        t3 = f" {_GLYPH_CLOCK} {now} "
        tb2 = d_dummy.textbbox((0, 0), t2, font=font)
        tb3 = d_dummy.textbbox((0, 0), t3, font=font)

        pill_w += (
            chev_w + (tb2[2] - tb2[0]) +
            chev_w + (tb3[2] - tb3[0]) +
            end_w
        )

        py_cmd = "python" if platform.system() == "Windows" else "python3"
        tb_pr = d_dummy.textbbox((0, 0), _GLYPH_PROMPT, font=font)
        tb_cmd = d_dummy.textbbox((0, 0), f"{py_cmd} {script_name}", font=font)
        top_w = pill_w + 6 + (tb_pr[2] - tb_pr[0]) + 12 + (tb_cmd[2] - tb_cmd[0]) + 40

        # Bottom bar trailing: 󰔛 {dur_str}❯ █
        dur_str = format_duration(duration_ms)
        dur_text = f" {_GLYPH_TIMER} {dur_str}"
        tb_dur = d_dummy.textbbox((0, 0), dur_text, font=font)
        tb_pr_bot = d_dummy.textbbox((0, 0), f"{_GLYPH_PROMPT} ", font=font)
        bot_w = pill_w + (tb_dur[2] - tb_dur[0]) + 4 + (tb_pr_bot[2] - tb_pr_bot[0]) + 4 + 9 + 40

        return max(top_w, bot_w)

    def render_prompt_bar(
        self,
        script_path: Optional[str | Path],
        script_name: str,
        duration_ms: float = 0.0,
        is_bottom: bool = False,
        width_px: int = 1000,
    ) -> Image.Image:
        """
        Render a pixel-perfect Starship prompt bar faithfully reproducing
        the user's Starship palette, OS icon, directory, and Powerline pill layout.
        """
        font = self.font
        dummy = Image.new("RGB", (1, 1))
        d_dummy = ImageDraw.Draw(dummy)

        needed_w = self.calculate_required_width(script_path, script_name, duration_ms)
        actual_w = max(width_px, needed_w)

        # Exact glyph metrics
        bb_cap = d_dummy.textbbox((0, 0), _GLYPH_LEFT_CAP, font=font)
        cap_h = bb_cap[3] - bb_cap[1]
        cap_w = bb_cap[2] - bb_cap[0]

        bb_chev = d_dummy.textbbox((0, 0), _GLYPH_CHEVRON, font=font)
        chev_w = bb_chev[2] - bb_chev[0]

        bb_end = d_dummy.textbbox((0, 0), _GLYPH_RIGHT_CAP, font=font)
        end_w = bb_end[2] - bb_end[0]

        username = get_current_username()
        os_glyph = get_os_glyph()
        dir_str = get_display_directory(script_path)
        py_ver = f"v{sys.version.split()[0]}"
        now = datetime.datetime.now().strftime("%I:%M %p").lstrip("0")
        if not now.startswith("1") and len(now) < 8:
            now = "0" + now

        # Segment contents
        t0 = f" {os_glyph}  {username} "
        t1 = f" {dir_str} "
        tb0 = d_dummy.textbbox((0, 0), t0, font=font)
        w0 = tb0[2] - tb0[0]
        tb1 = d_dummy.textbbox((0, 0), t1, font=font)
        w1 = tb1[2] - tb1[0]

        sp = Path(script_path) if script_path else None
        git_branch = get_git_branch(sp.parent if sp and (sp.is_file() or sp.suffix) else sp)
        if git_branch:
            t_git = f" {_GLYPH_GIT} {git_branch} "
            tb_git = d_dummy.textbbox((0, 0), t_git, font=font)
            w_git = tb_git[2] - tb_git[0]
        else:
            t_git = ""
            tb_git = None
            w_git = 0

        t2 = f" {_GLYPH_PYTHON} {py_ver} "
        t3 = f" {_GLYPH_CLOCK} {now} "

        tb2 = d_dummy.textbbox((0, 0), t2, font=font)
        w2 = tb2[2] - tb2[0]

        tb3 = d_dummy.textbbox((0, 0), t3, font=font)
        w3 = tb3[2] - tb3[0]

        bar_total_h = cap_h + 12
        y0 = 6  # vertical center inside bar
        img = Image.new("RGB", (actual_w, bar_total_h), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        x = 0

        # 1. Left cap (glass0)
        draw.text((x - bb_cap[0], y0 - bb_cap[1]), _GLYPH_LEFT_CAP, font=font, fill=self.glass0)
        x += cap_w

        # 2. Seg 0: OS + username (glass0)
        draw.rectangle([(x, y0), (x + w0, y0 + cap_h)], fill=self.glass0)
        draw.text((x, _center_y(y0, cap_h, tb0)), t0, font=font, fill=self.text_col)
        x += w0

        # 3. Chevron 0 -> 1
        draw.rectangle([(x, y0), (x + chev_w, y0 + cap_h)], fill=self.glass1)
        draw.text((x - bb_chev[0], y0 - bb_chev[1]), _GLYPH_CHEVRON, font=font, fill=self.glass0)
        x += chev_w

        # 4. Seg 1: Directory (glass1)
        draw.rectangle([(x, y0), (x + w1, y0 + cap_h)], fill=self.glass1)
        draw.text((x, _center_y(y0, cap_h, tb1)), t1, font=font, fill=self.text_col)
        x += w1

        # 5. Optional Seg 2: Git branch (glass2)
        if git_branch and tb_git is not None:
            # Chevron 1 -> 2
            draw.rectangle([(x, y0), (x + chev_w, y0 + cap_h)], fill=self.glass2)
            draw.text((x - bb_chev[0], y0 - bb_chev[1]), _GLYPH_CHEVRON, font=font, fill=self.glass1)
            x += chev_w

            draw.rectangle([(x, y0), (x + w_git, y0 + cap_h)], fill=self.glass2)
            draw.text((x, _center_y(y0, cap_h, tb_git)), t_git, font=font, fill=self.text_col)
            x += w_git

            # Chevron 2 -> 3
            draw.rectangle([(x, y0), (x + chev_w, y0 + cap_h)], fill=self.glass3)
            draw.text((x - bb_chev[0], y0 - bb_chev[1]), _GLYPH_CHEVRON, font=font, fill=self.glass2)
            x += chev_w
        else:
            # Chevron 1 -> 3
            draw.rectangle([(x, y0), (x + chev_w, y0 + cap_h)], fill=self.glass3)
            draw.text((x - bb_chev[0], y0 - bb_chev[1]), _GLYPH_CHEVRON, font=font, fill=self.glass1)
            x += chev_w

        # 6. Seg 3: Python (glass3)
        draw.rectangle([(x, y0), (x + w2, y0 + cap_h)], fill=self.glass3)
        draw.text((x, _center_y(y0, cap_h, tb2)), t2, font=font, fill=self.text_col)
        x += w2

        # 7. Chevron 3 -> 5
        draw.rectangle([(x, y0), (x + chev_w, y0 + cap_h)], fill=self.glass5)
        draw.text((x - bb_chev[0], y0 - bb_chev[1]), _GLYPH_CHEVRON, font=font, fill=self.glass3)
        x += chev_w

        # 8. Seg 4: Time (glass5)
        draw.rectangle([(x, y0), (x + w3, y0 + cap_h)], fill=self.glass5)
        draw.text((x, _center_y(y0, cap_h, tb3)), t3, font=font, fill=self.text_col)
        x += w3

        # 9. Right cap (glass5)
        draw.text((x - bb_end[0], y0 - bb_end[1]), _GLYPH_RIGHT_CAP, font=font, fill=self.glass5)
        x += end_w

        # 10. Trailing elements
        if not is_bottom:
            # Top line: ❯ python3 script.py (or python on Windows)
            import platform
            py_cmd = "python" if platform.system() == "Windows" else "python3"
            tb_pr = d_dummy.textbbox((0, 0), _GLYPH_PROMPT, font=font)
            y_pr = _center_y(y0, cap_h, tb_pr)
            draw.text((x + 6, y_pr), _GLYPH_PROMPT, font=font, fill=self.text_col)
            x += (tb_pr[2] - tb_pr[0]) + 12

            cmd_text = f"{py_cmd} {script_name}"
            tb_cmd = d_dummy.textbbox((0, 0), cmd_text, font=font)
            y_cmd = _center_y(y0, cap_h, tb_cmd)
            draw.text((x, y_cmd), cmd_text, font=font, fill=(245, 245, 245))
        else:
            # Bottom line: ALWAYS show execution duration for every block
            dur_str = format_duration(duration_ms)
            dur_text = f" {_GLYPH_TIMER} {dur_str}"
            tb_dur = d_dummy.textbbox((0, 0), dur_text, font=font)
            y_dur = _center_y(y0, cap_h, tb_dur)
            draw.text((x, y_dur), dur_text, font=font, fill=self.dim_col)
            x += tb_dur[2] - tb_dur[0]

            tb_pr = d_dummy.textbbox((0, 0), f"{_GLYPH_PROMPT} ", font=font)
            y_pr = _center_y(y0, cap_h, tb_pr)
            draw.text((x + 4, y_pr), f"{_GLYPH_PROMPT} ", font=font, fill=self.text_col)
            x += (tb_pr[2] - tb_pr[0]) + 4

            # Cursor block
            cursor_w = 9
            draw.rectangle([(x, y0 + 2), (x + cursor_w, y0 + cap_h - 2)], fill=(200, 206, 216))

        return img
