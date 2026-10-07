"""
gui.py - Native Desktop Studio GUI for PyBlockRunner

A professional dark-themed IDE Studio application window.

Features:
  - Modern Obsidian/Slate Studio theme matching developer-grade IDEs
  - Collapsible 4-column execution configuration panel
  - Real-time execution status bar with prominent Emerald 'Run Script' and Cyan 'Open PDF' actions
  - Two-column responsive workspace:
      * Left: Rich scrollable block cards (with LOC, code snippet, custom check toggles) + live process log
      * Right: High-resolution block screenshot preview + PDF dossier artifact card
  - Universal drag-and-drop file loading across the entire window
  - Interactive input modal, interactive font chooser, and AST dependency preservation

Thread safety: all execution runs in a daemon background thread.
GUI updates come only via root.after() calls, never directly from the thread.
"""

from __future__ import annotations

import argparse
import datetime
import os
import platform
import queue
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

import tkinter as tk
import tkinter.font as tkfont

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_TKDND = True
except ImportError:
    _HAS_TKDND = False

from PIL import Image, ImageTk

from .parser import parse_file, Block
from .executor import Executor, BlockResult
from .pdf_builder import build_pdf
from .docx_builder import build_docx, is_docx_available


# ---------------------------------------------------------------------------
# Studio Dark Palette
# ---------------------------------------------------------------------------

BG_ROOT          = "#0a0e17"   # Master window background
BG_PANEL         = "#111827"   # Secondary panels & bars
BG_CARD          = "#141c2b"   # Main section cards
BG_CARD_ALT      = "#182234"   # Inner block cards
BG_CARD_HOVER    = "#1e2a40"   # Hover state for block cards
BG_CARD_ACTIVE   = "#1f2d48"   # Selected/clicked block card
BG_INPUT         = "#0c121e"   # Input entries, comboboxes
BG_CODE_BOX      = "#080c14"   # Code snippet & console backgrounds
BORDER_COLOR     = "#202b3e"   # Subtle card borders
BORDER_ACTIVE    = "#38bdf8"   # Focused / active border

FG_WHITE         = "#f8fafc"   # Primary headings & titles
FG_TEXT          = "#e2e8f0"   # Primary body text
FG_MUTED         = "#8292a8"   # Secondary text / labels
FG_DIM           = "#4e5d73"   # Placeholders / inactive

ACCENT_GREEN     = "#10b981"   # Emerald Run button & Active badges
ACCENT_GREEN_BG  = "#064e3b"   # Green pill background
ACCENT_GREEN_FG  = "#34d399"   # Green pill foreground
ACCENT_CYAN      = "#0ea5e9"   # PDF button, tags, target
ACCENT_CYAN_BG   = "#0c4a6e"   # Cyan pill background
ACCENT_CYAN_FG   = "#38bdf8"   # Cyan pill foreground
ACCENT_AMBER     = "#f59e0b"   # Warnings / durations
ACCENT_AMBER_BG  = "#451a03"
ACCENT_AMBER_FG  = "#fbbf24"
ACCENT_RED       = "#ef4444"   # Errors / stop button
ACCENT_RED_BG    = "#450a0a"
ACCENT_RED_FG    = "#f87171"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PREVIEW_MAX_W = 540
PREVIEW_MAX_H = 340
POLL_MS = 60   # How often to drain the event queue (milliseconds)


# ---------------------------------------------------------------------------
# Helper: clean dropped path
# ---------------------------------------------------------------------------

def _clean_dropped_path(raw: str) -> str:
    """Clean a dropped or pasted file path string from URIs, quotes, braces, and escape sequences."""
    s = str(raw).strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1].strip()
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        s = s[1:-1].strip()
    if s.startswith("file://localhost/"):
        s = "/" + urllib.parse.unquote(s[17:])
    elif s.startswith("file:///"):
        s = "/" + urllib.parse.unquote(s[8:])
    elif s.startswith("file://"):
        s = urllib.parse.unquote(s[7:])
    s = urllib.parse.unquote(s).strip()
    if not Path(s).exists() and "\\ " in s:
        unescaped = s.replace("\\ ", " ")
        if Path(unescaped).exists():
            return unescaped
    return s


# ---------------------------------------------------------------------------
# Helper: open PDF in system viewer
# ---------------------------------------------------------------------------

def _open_file(path: Path) -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif platform.system() == "Windows":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


_open_pdf = _open_file


# ---------------------------------------------------------------------------
# Custom Styled Studio Widgets (Zero White Background Artifacts on macOS)
# ---------------------------------------------------------------------------

class StudioButton(tk.Label):
    """
    A custom dark button built on tk.Label to ensure consistent, rich colors
    across all platforms without macOS Aqua white button artifacts.
    """
    def __init__(
        self,
        parent,
        text: str,
        command=None,
        bg: str = "#1e293b",
        fg: str = "#e2e8f0",
        hover_bg: str = "#2c3b52",
        disabled_bg: str = "#151c27",
        disabled_fg: str = "#455368",
        font=("Helvetica", 10, "bold"),
        padx: int = 12,
        pady: int = 6,
        highlightthickness: int = 1,
        highlightbackground: str = BORDER_COLOR,
        state: str = "normal",
        **kwargs
    ):
        super().__init__(
            parent,
            text=text,
            bg=bg if state == "normal" else disabled_bg,
            fg=fg if state == "normal" else disabled_fg,
            font=font,
            padx=padx,
            pady=pady,
            cursor="hand2" if state == "normal" else "arrow",
            highlightthickness=highlightthickness,
            highlightbackground=highlightbackground,
            **kwargs
        )
        self._command = command
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg
        self._state = state
        self._active = False

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_enter(self, e):
        if self._state == "normal":
            super().config(bg=self._hover_bg)

    def _on_leave(self, e):
        if self._state == "normal":
            super().config(bg=self._bg)

    def _on_click(self, e):
        if self._state == "normal":
            self._active = True
            super().config(bg=self._hover_bg)

    def _on_release(self, e):
        if self._state == "normal" and self._active:
            self._active = False
            super().config(bg=self._bg)
            if self._command:
                self._command()

    def set_state(self, state: str):
        self._state = state
        if state == "normal":
            super().config(bg=self._bg, fg=self._fg, cursor="hand2")
        else:
            super().config(bg=self._disabled_bg, fg=self._disabled_fg, cursor="arrow")

    def config(self, **kwargs):
        if "state" in kwargs:
            self.set_state(kwargs.pop("state"))
        if "bg" in kwargs and self._state == "normal":
            self._bg = kwargs["bg"]
        if "fg" in kwargs and self._state == "normal":
            self._fg = kwargs["fg"]
        super().config(**kwargs)


class BlockCheckbox(tk.Label):
    """
    A custom checkbox for block cards.
    """
    def __init__(self, parent, variable: tk.BooleanVar, command=None):
        self.var = variable
        self.command = command
        val = variable.get()
        super().__init__(
            parent,
            text="✓" if val else " ",
            font=("Helvetica", 10, "bold"),
            bg="#0ea5e9" if val else "#0c121e",
            fg="#ffffff" if val else "#4e5d73",
            width=2,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#0ea5e9" if val else "#202b3e",
            cursor="hand2",
        )
        self.bind("<Button-1>", self._on_click)
        self.var.trace_add("write", lambda *_: self._update())

    def _update(self):
        val = self.var.get()
        self.config(
            text="✓" if val else " ",
            bg="#0ea5e9" if val else "#0c121e",
            fg="#ffffff" if val else "#4e5d73",
            highlightbackground="#0ea5e9" if val else "#202b3e",
        )

    def _on_click(self, e):
        self.var.set(not self.var.get())
        if self.command:
            self.command()


class ToggleSwitch(tk.Canvas):
    """
    A modern iOS/Studio pill toggle switch.
    """
    def __init__(self, parent, variable: tk.BooleanVar, command=None, width=44, height=22, **kwargs):
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2", **kwargs)
        self.var = variable
        self.command = command
        self._width_px = width
        self._height_px = height
        self.bind("<Button-1>", self._on_toggle)
        self.var.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _draw(self):
        self.delete("all")
        val = self.var.get()
        bg_col = "#10b981" if val else "#243144"
        r = self._height_px // 2
        # Pill body
        self.create_oval(1, 1, self._height_px - 1, self._height_px - 1, fill=bg_col, outline=bg_col)
        self.create_oval(self._width_px - self._height_px + 1, 1, self._width_px - 1, self._height_px - 1, fill=bg_col, outline=bg_col)
        self.create_rectangle(r, 1, self._width_px - r, self._height_px - 1, fill=bg_col, outline=bg_col)
        # Knob
        kx = self._width_px - r - 2 if val else r + 2
        self.create_oval(kx - r + 3, 3, kx + r - 3, self._height_px - 3, fill="#ffffff", outline="#ffffff")

    def _on_toggle(self, e):
        self.var.set(not self.var.get())
        if self.command:
            self.command()


# ---------------------------------------------------------------------------
# Font Chooser Modal Dialog
# ---------------------------------------------------------------------------

class FontChooserDialog(tk.Toplevel):
    """Interactive font chooser dialog listing all installed system fonts with live preview."""

    def __init__(self, parent: tk.Tk, current_font: str, on_select):
        super().__init__(parent)
        self.title("Select Document Font")
        self.geometry("480x540")
        self.minsize(420, 440)
        self.configure(bg=BG_CARD)
        self.transient(parent)
        self.grab_set()

        self._on_select = on_select
        try:
            raw_fonts = sorted(set(tkfont.families()))
        except Exception:
            raw_fonts = []
        self._all_fonts = [f for f in raw_fonts if not f.startswith("@")]

        # Search bar
        top_f = tk.Frame(self, bg=BG_CARD, padx=12, pady=10)
        top_f.pack(fill="x")
        tk.Label(top_f, text="Search Font:", font=("Helvetica", 10, "bold"), fg=FG_TEXT, bg=BG_CARD).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._filter_fonts)
        search_entry = tk.Entry(
            top_f, textvariable=self._search_var, bg=BG_INPUT, fg=FG_WHITE,
            insertbackground=FG_WHITE, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BORDER_COLOR, highlightcolor=ACCENT_CYAN,
            font=("Helvetica", 11)
        )
        search_entry.pack(side="left", fill="x", expand=True, padx=8)
        search_entry.focus_set()

        # Font listbox
        list_f = tk.Frame(self, bg=BG_CARD, padx=12)
        list_f.pack(fill="both", expand=True)
        self._listbox = tk.Listbox(
            list_f, selectmode="single", exportselection=False,
            font=("Helvetica", 11), bg=BG_INPUT, fg=FG_TEXT,
            selectbackground=ACCENT_CYAN_BG, selectforeground=FG_WHITE,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR
        )
        scrollbar = ttk.Scrollbar(list_f, orient="vertical", command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=scrollbar.set)
        self._listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._listbox.bind("<<ListboxSelect>>", self._on_select_item)
        self._listbox.bind("<Double-Button-1>", lambda e: self._choose())

        # Preview frame
        prev_f = tk.Frame(self, bg=BG_CARD_ALT, padx=10, pady=10, highlightthickness=1, highlightbackground=BORDER_COLOR)
        prev_f.pack(fill="x", padx=12, pady=10)
        tk.Label(prev_f, text="FONT PREVIEW", font=("Helvetica", 8, "bold"), fg=FG_MUTED, bg=BG_CARD_ALT).pack(anchor="w")
        self._preview_lbl = tk.Label(
            prev_f,
            text="The quick brown fox jumps over the lazy dog 12345",
            font=("Helvetica", 12),
            fg=FG_WHITE,
            bg=BG_CARD_ALT,
            wraplength=440,
            justify="center",
            pady=6,
        )
        self._preview_lbl.pack(fill="x")

        # Bottom actions
        btn_f = tk.Frame(self, bg=BG_CARD, padx=12, pady=10)
        btn_f.pack(fill="x")
        if platform.system() == "Darwin":
            StudioButton(
                btn_f, text="Open Font Book", command=self._open_font_book,
                bg=BG_CARD_ALT, fg=FG_TEXT, hover_bg=BORDER_COLOR, padx=10, pady=4
            ).pack(side="left")

        StudioButton(
            btn_f, text="Reset Default", command=self._reset_default,
            bg=BG_CARD_ALT, fg=FG_TEXT, hover_bg=BORDER_COLOR, padx=10, pady=4
        ).pack(side="left", padx=6)

        StudioButton(
            btn_f, text="Select Font", command=self._choose,
            bg=ACCENT_CYAN, fg="#ffffff", hover_bg="#0284c7",
            font=("Helvetica", 10, "bold"), padx=14, pady=4
        ).pack(side="right")

        StudioButton(
            btn_f, text="Cancel", command=self.destroy,
            bg=BG_CARD_ALT, fg=FG_MUTED, hover_bg=BORDER_COLOR, padx=10, pady=4
        ).pack(side="right", padx=6)

        self._populate_list(self._all_fonts)
        if current_font and current_font in self._all_fonts:
            idx = self._all_fonts.index(current_font)
            self._listbox.selection_set(idx)
            self._listbox.see(idx)
            self._update_preview(current_font)
        elif "Iosevka Nerd Font" in self._all_fonts:
            idx = self._all_fonts.index("Iosevka Nerd Font")
            self._listbox.selection_set(idx)
            self._listbox.see(idx)
            self._update_preview("Iosevka Nerd Font")

    def _populate_list(self, fonts: list[str]) -> None:
        self._listbox.delete(0, "end")
        for f in fonts:
            self._listbox.insert("end", f)

    def _filter_fonts(self, *args) -> None:
        query = self._search_var.get().strip().lower()
        filtered = [f for f in self._all_fonts if query in f.lower()]
        self._populate_list(filtered)

    def _on_select_item(self, event) -> None:
        sel = self._listbox.curselection()
        if sel:
            font_name = self._listbox.get(sel[0])
            self._update_preview(font_name)

    def _update_preview(self, font_name: str) -> None:
        try:
            self._preview_lbl.config(font=(font_name, 12))
        except Exception:
            pass

    def _open_font_book(self) -> None:
        try:
            subprocess.Popen(["open", "-a", "Font Book"])
        except Exception:
            pass

    def _reset_default(self) -> None:
        self._on_select("")
        self.destroy()

    def _choose(self) -> None:
        sel = self._listbox.curselection()
        if sel:
            font_name = self._listbox.get(sel[0])
            self._on_select(font_name)
        self.destroy()


# ---------------------------------------------------------------------------
# Interactive User Input Prompt Dialog
# ---------------------------------------------------------------------------

class InputDialog(tk.Toplevel):
    """
    Modal dialog prompting the user for input when an input() call is executed
    within a code block.
    """

    def __init__(
        self,
        parent: tk.Tk,
        prompt: str,
        block_title: str = "",
        block_index: int = 0,
        theme: str = "light",
        on_submit: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self.title("User Input Required")
        self.configure(bg=BG_CARD)
        self.transient(parent)
        self.resizable(False, False)
        self._on_submit = on_submit
        self._submitted = False

        container = tk.Frame(self, bg=BG_CARD, padx=18, pady=18)
        container.pack(fill="both", expand=True)

        header_text = f"Block {block_index}" if block_index > 0 else "Script"
        if block_title:
            header_text += f": {block_title}"

        lbl_header = tk.Label(
            container,
            text=f"⌨  {header_text}",
            font=("Helvetica", 12, "bold"),
            fg=ACCENT_CYAN_FG,
            bg=BG_CARD,
        )
        lbl_header.pack(anchor="w", pady=(0, 6))

        clean_prompt = prompt.rstrip("\r\n") if prompt else "Please enter value:"
        lbl_prompt = tk.Label(
            container,
            text=clean_prompt,
            font=("Helvetica", 11),
            fg=FG_WHITE,
            bg=BG_CARD,
            wraplength=440,
            justify="left",
        )
        lbl_prompt.pack(anchor="w", pady=(0, 12))

        self._entry_var = tk.StringVar()
        self._entry = tk.Entry(
            container,
            textvariable=self._entry_var,
            width=48,
            font=("Courier New", 12),
            bg=BG_INPUT,
            fg=FG_WHITE,
            insertbackground=FG_WHITE,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=ACCENT_CYAN,
        )
        self._entry.pack(fill="x", ipady=4, pady=(0, 16))
        self._entry.bind("<Return>", lambda _: self._submit())
        self._entry.bind("<Escape>", lambda _: self._cancel())

        btn_box = tk.Frame(container, bg=BG_CARD)
        btn_box.pack(fill="x")

        StudioButton(
            btn_box, text="Cancel", command=self._cancel,
            bg=BG_CARD_ALT, fg=FG_MUTED, hover_bg=BORDER_COLOR, padx=12, pady=5
        ).pack(side="right", padx=(8, 0))

        StudioButton(
            btn_box, text="Submit Input", command=self._submit,
            bg=ACCENT_GREEN, fg="#ffffff", hover_bg="#059669",
            font=("Helvetica", 10, "bold"), padx=16, pady=5
        ).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        w = max(480, self.winfo_reqwidth())
        h = max(170, self.winfo_reqheight())
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.lift()
        self.grab_set()
        self._entry.focus_set()

    def _submit(self) -> None:
        if not self._submitted:
            self._submitted = True
            val = self._entry_var.get()
            if self._on_submit:
                self._on_submit(val)
        self.destroy()

    def _cancel(self) -> None:
        if not self._submitted:
            self._submitted = True
            if self._on_submit:
                self._on_submit("")
        self.destroy()


# ---------------------------------------------------------------------------
# Main Studio Desktop Application
# ---------------------------------------------------------------------------

class PyBlockRunnerApp:
    def __init__(self, root: tk.Tk, prefill_script: Optional[str] = None):
        self.root = root
        self.root.title("PyBlockRunner Studio")
        self.root.geometry("1240x820")
        self.root.minsize(960, 640)
        self.root.configure(bg=BG_ROOT)

        # Message queue for thread → GUI communication
        self._q: queue.Queue = queue.Queue()

        # State
        self._blocks: list[Block] = []
        self._block_vars: list[tk.BooleanVar] = []
        self._block_card_widgets: list[dict] = []
        self._results: list[BlockResult] = []
        self._running = False
        self._out_path: Optional[Path] = None
        self._docx_path: Optional[Path] = None
        self._preview_photo: Optional[ImageTk.PhotoImage] = None
        self._selected_block_idx = 0
        self._config_expanded = True

        self._init_ttk_styles()
        self._build_ui()
        self._setup_drag_and_drop()

        # Keyboard shortcuts
        self.root.bind("<Command-Return>", lambda _: self._start_run())
        self.root.bind("<Control-Return>", lambda _: self._start_run())
        self.root.bind("<Command-o>", lambda _: self._browse())
        self.root.bind("<Control-o>", lambda _: self._browse())

        if prefill_script:
            self._script_var.set(prefill_script)
            self._on_file_selected()

        # Start the GUI event-queue polling loop
        self.root.after(POLL_MS, self._poll_queue)

    def _init_ttk_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            "Vertical.TScrollbar",
            background=BG_CARD_ALT,
            troughcolor=BG_ROOT,
            bordercolor=BORDER_COLOR,
            arrowcolor=FG_MUTED,
            relief="flat",
        )

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)  # Two-column workspace expands

        # ── 1. Configuration Section (Collapsible) ─────────────────────
        self._config_container = tk.Frame(root, bg=BG_CARD, padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER_COLOR)
        self._config_container.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        self._config_container.columnconfigure(0, weight=1)

        # Header of Config Card
        c_hdr = tk.Frame(self._config_container, bg=BG_CARD)
        c_hdr.pack(fill="x", pady=(0, 8))

        self._toggle_hdr_btn = StudioButton(
            c_hdr,
            text="▼  Execution Context & Report Generator Configuration",
            command=self._toggle_config_panel,
            bg=BG_CARD,
            fg=FG_WHITE,
            hover_bg=BG_CARD_ALT,
            font=("Helvetica", 11, "bold"),
            padx=4,
            pady=2,
            highlightthickness=0,
        )
        self._toggle_hdr_btn.pack(side="left")

        self._toggle_config_btn = StudioButton(
            c_hdr,
            text="▲  Collapse Config",
            command=self._toggle_config_panel,
            bg=BG_CARD_ALT,
            fg=FG_MUTED,
            hover_bg=BORDER_COLOR,
            font=("Helvetica", 8, "bold"),
            padx=10,
            pady=3,
        )
        self._toggle_config_btn.pack(side="right")

        self._config_body = tk.Frame(self._config_container, bg=BG_CARD)
        self._config_body.pack(fill="x")

        # Row 1: Script File Path
        script_row = tk.Frame(self._config_body, bg=BG_CARD)
        script_row.pack(fill="x", pady=(0, 8))

        lbl_s_box = tk.Frame(script_row, bg=BG_CARD)
        lbl_s_box.pack(fill="x", pady=(0, 4))
        tk.Label(
            lbl_s_box,
            text="📄  Script File Path",
            font=("Helvetica", 10, "bold"),
            fg=ACCENT_CYAN_FG,
            bg=BG_CARD,
        ).pack(side="left")
        tk.Label(
            lbl_s_box,
            text="💡 Drop script or browse filesystem",
            font=("Helvetica", 9),
            fg=FG_MUTED,
            bg=BG_CARD,
        ).pack(side="right")

        input_box = tk.Frame(script_row, bg=BG_CARD)
        input_box.pack(fill="x")

        self._script_var = tk.StringVar()
        self._script_entry = tk.Entry(
            input_box,
            textvariable=self._script_var,
            font=("Courier New", 11),
            bg=BG_INPUT,
            fg=FG_WHITE,
            insertbackground=FG_WHITE,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=ACCENT_CYAN,
        )
        self._script_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        self._script_entry.bind("<Return>", lambda _: self._on_file_selected())
        self._script_entry.bind("<FocusOut>", lambda _: self._on_file_selected())
        self._script_entry.bind("<KeyRelease>", lambda _: self.root.after(200, self._check_auto_parse))

        self._ast_pill = tk.Label(
            input_box,
            text="AWAITING FILE",
            font=("Helvetica", 9, "bold"),
            fg=FG_MUTED,
            bg=BG_CARD_ALT,
            padx=8,
            pady=5,
        )
        self._ast_pill.pack(side="left", padx=(0, 6))

        StudioButton(
            input_box,
            text="📂  Browse File",
            command=self._browse,
            bg="#1e293b",
            fg=FG_WHITE,
            hover_bg=BORDER_COLOR,
            font=("Helvetica", 9, "bold"),
            padx=12,
            pady=5,
        ).pack(side="left", padx=(0, 4))

        StudioButton(
            input_box,
            text="🔄",
            command=self._on_file_selected,
            bg="#1e293b",
            fg=FG_WHITE,
            hover_bg=BORDER_COLOR,
            font=("Helvetica", 9),
            padx=8,
            pady=5,
        ).pack(side="left")

        # Row 2: 4 Grid Columns
        grid_row = tk.Frame(self._config_body, bg=BG_CARD)
        grid_row.pack(fill="x", pady=(4, 0))
        for c in range(4):
            grid_row.columnconfigure(c, weight=1)

        # Col 1: REPORT METADATA
        c1 = tk.Frame(grid_row, bg=BG_CARD_ALT, padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER_COLOR)
        c1.grid(row=0, column=0, sticky="nsew", padx=3)
        tk.Label(c1, text="REPORT METADATA", font=("Helvetica", 9, "bold"), fg=FG_MUTED, bg=BG_CARD_ALT).pack(anchor="w", pady=(0, 4))
        tk.Label(c1, text="Name (Author / Student)", font=("Helvetica", 8), fg=FG_DIM, bg=BG_CARD_ALT).pack(anchor="w")
        self._name_var = tk.StringVar()
        tk.Entry(
            c1, textvariable=self._name_var, font=("Helvetica", 10), bg=BG_INPUT, fg=FG_WHITE,
            insertbackground=FG_WHITE, relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR
        ).pack(fill="x", ipady=3, pady=(1, 5))
        tk.Label(c1, text="ID / Roll No (Optional)", font=("Helvetica", 8), fg=FG_DIM, bg=BG_CARD_ALT).pack(anchor="w")
        self._id_var = tk.StringVar()
        tk.Entry(
            c1, textvariable=self._id_var, font=("Helvetica", 10), bg=BG_INPUT, fg=FG_WHITE,
            insertbackground=FG_WHITE, relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR
        ).pack(fill="x", ipady=3, pady=(1, 0))

        # Col 2: ENVIRONMENT & THEME
        c2 = tk.Frame(grid_row, bg=BG_CARD_ALT, padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER_COLOR)
        c2.grid(row=0, column=1, sticky="nsew", padx=3)
        tk.Label(c2, text="ENVIRONMENT & THEME", font=("Helvetica", 9, "bold"), fg=FG_MUTED, bg=BG_CARD_ALT).pack(anchor="w", pady=(0, 4))
        tk.Label(c2, text="Theme Profile", font=("Helvetica", 8), fg=FG_DIM, bg=BG_CARD_ALT).pack(anchor="w")
        self._theme_var = tk.StringVar(value="light")
        om_theme = tk.OptionMenu(c2, self._theme_var, "light", "dark")
        om_theme.config(
            bg=BG_INPUT, fg=FG_WHITE, activebackground=BORDER_COLOR, activeforeground=FG_WHITE,
            highlightthickness=1, highlightbackground=BORDER_COLOR, bd=0, font=("Helvetica", 9), cursor="hand2"
        )
        om_theme["menu"].config(bg=BG_CARD, fg=FG_WHITE, activebackground=ACCENT_CYAN, activeforeground="#ffffff", bd=0)
        om_theme.pack(fill="x", pady=(1, 5))

        tk.Label(c2, text="State Isolation Mode", font=("Helvetica", 8), fg=FG_DIM, bg=BG_CARD_ALT).pack(anchor="w")
        self._state_var = tk.StringVar(value="Shared")
        om_state = tk.OptionMenu(c2, self._state_var, "Shared", "Isolated")
        om_state.config(
            bg=BG_INPUT, fg=FG_WHITE, activebackground=BORDER_COLOR, activeforeground=FG_WHITE,
            highlightthickness=1, highlightbackground=BORDER_COLOR, bd=0, font=("Helvetica", 9), cursor="hand2"
        )
        om_state["menu"].config(bg=BG_CARD, fg=FG_WHITE, activebackground=ACCENT_CYAN, activeforeground="#ffffff", bd=0)
        om_state.pack(fill="x", pady=(1, 0))

        # Col 3: PDF SCOPE & LIMITS
        c3 = tk.Frame(grid_row, bg=BG_CARD_ALT, padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER_COLOR)
        c3.grid(row=0, column=2, sticky="nsew", padx=3)
        tk.Label(c3, text="PDF SCOPE & LIMITS", font=("Helvetica", 9, "bold"), fg=FG_MUTED, bg=BG_CARD_ALT).pack(anchor="w", pady=(0, 4))

        sw1_f = tk.Frame(c3, bg=BG_CARD_ALT)
        sw1_f.pack(fill="x", pady=2)
        tk.Label(sw1_f, text="Include Code Listing", font=("Helvetica", 9), fg=FG_TEXT, bg=BG_CARD_ALT).pack(side="left")
        self._code_var = tk.BooleanVar(value=False)
        ToggleSwitch(sw1_f, self._code_var, width=38, height=18).pack(side="right")

        sw2_f = tk.Frame(c3, bg=BG_CARD_ALT)
        sw2_f.pack(fill="x", pady=2)
        tk.Label(sw2_f, text="Generate Cover Page", font=("Helvetica", 9), fg=FG_TEXT, bg=BG_CARD_ALT).pack(side="left")
        self._cover_var = tk.BooleanVar(value=False)
        ToggleSwitch(sw2_f, self._cover_var, width=38, height=18).pack(side="right")

        to_box = tk.Frame(c3, bg=BG_CARD_ALT)
        to_box.pack(fill="x", pady=(4, 0))
        tk.Label(to_box, text="Timeout (sec):", font=("Helvetica", 8), fg=FG_DIM, bg=BG_CARD_ALT).pack(side="left")
        self._timeout_var = tk.StringVar(value="30")
        tk.Entry(
            to_box, textvariable=self._timeout_var, width=5, font=("Helvetica", 9), bg=BG_INPUT, fg=FG_WHITE,
            insertbackground=FG_WHITE, relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR
        ).pack(side="left", padx=4)

        # Col 4: OUTPUT ARTIFACTS
        c4 = tk.Frame(grid_row, bg=BG_CARD_ALT, padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER_COLOR)
        c4.grid(row=0, column=3, sticky="nsew", padx=3)
        tk.Label(c4, text="OUTPUT ARTIFACTS", font=("Helvetica", 9, "bold"), fg=FG_MUTED, bg=BG_CARD_ALT).pack(anchor="w", pady=(0, 4))
        tk.Label(c4, text="Output PDF Dossier", font=("Helvetica", 8), fg=FG_DIM, bg=BG_CARD_ALT).pack(anchor="w")
        out_f = tk.Frame(c4, bg=BG_CARD_ALT)
        out_f.pack(fill="x", pady=(1, 5))
        self._outpath_var = tk.StringVar()
        tk.Entry(
            out_f, textvariable=self._outpath_var, font=("Helvetica", 9), bg=BG_INPUT, fg=FG_WHITE,
            insertbackground=FG_WHITE, relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER_COLOR
        ).pack(side="left", fill="x", expand=True, ipady=3)
        StudioButton(
            out_f, text="📂", command=self._browse_output, font=("Helvetica", 8), bg="#1e293b", fg=FG_WHITE,
            hover_bg=BORDER_COLOR, padx=6, pady=2
        ).pack(side="left", padx=(2, 0))

        sw_docx_f = tk.Frame(c4, bg=BG_CARD_ALT)
        sw_docx_f.pack(fill="x", pady=(2, 4))
        tk.Label(sw_docx_f, text="Export Word (.docx)", font=("Helvetica", 8), fg=FG_DIM, bg=BG_CARD_ALT).pack(side="left")
        self._docx_var = tk.BooleanVar(value=True)
        ToggleSwitch(sw_docx_f, self._docx_var, width=34, height=16).pack(side="right")

        font_f = tk.Frame(c4, bg=BG_CARD_ALT)
        font_f.pack(fill="x", pady=(2, 0))
        tk.Label(font_f, text="Doc Font:", font=("Helvetica", 8), fg=FG_DIM, bg=BG_CARD_ALT).pack(side="left")
        self._font_var = tk.StringVar(value="")
        self._font_display_var = tk.StringVar(value="Iosevka NF / Sys")
        tk.Label(
            font_f, textvariable=self._font_display_var, font=("Helvetica", 9, "bold"), fg=ACCENT_CYAN_FG, bg=BG_CARD_ALT
        ).pack(side="left", padx=4)
        StudioButton(
            font_f, text="Change...", command=self._open_font_chooser, font=("Helvetica", 8, "bold"),
            bg="#1e293b", fg=FG_TEXT, hover_bg=BORDER_COLOR, padx=8, pady=2
        ).pack(side="right")

        # ── 2. Action & Status Bar ─────────────────────────────────────
        action_bar = tk.Frame(root, bg=BG_PANEL, padx=14, pady=8, highlightthickness=1, highlightbackground=BORDER_COLOR)
        action_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 6))

        # Status indicator
        self._status_badge = tk.Label(
            action_bar,
            text="🟢  Ready for Execution",
            font=("Helvetica", 11, "bold"),
            fg=ACCENT_GREEN_FG,
            bg=ACCENT_GREEN_BG,
            padx=12,
            pady=6,
        )
        self._status_badge.pack(side="left")

        # Count badge
        self._blocks_stat_pill = tk.Label(
            action_bar,
            text="● Blocks: 0 / 0 Active",
            font=("Helvetica", 9, "bold"),
            fg=FG_TEXT,
            bg="#1f293d",
            padx=10,
            pady=6,
        )
        self._blocks_stat_pill.pack(side="left", padx=8)

        # Duration stat
        self._duration_lbl = tk.Label(
            action_bar,
            text="⏱  Duration: 0.0s",
            font=("Helvetica", 9),
            fg=FG_MUTED,
            bg=BG_PANEL,
        )
        self._duration_lbl.pack(side="left", padx=8)

        # Action buttons on right (Custom StudioButton with rich colors)
        self._run_btn = StudioButton(
            action_bar,
            text="▶  Run Script   Ctrl+↵",
            command=self._start_run,
            font=("Helvetica", 11, "bold"),
            bg=ACCENT_GREEN,
            fg="#ffffff",
            hover_bg="#059669",
            disabled_bg="#0d2b20",
            disabled_fg="#356853",
            padx=20,
            pady=7,
        )
        self._run_btn.pack(side="right")

        self._open_btn = StudioButton(
            action_bar,
            text="📄  Open PDF",
            command=self._open_result,
            font=("Helvetica", 11, "bold"),
            bg=ACCENT_CYAN,
            fg="#ffffff",
            hover_bg="#0284c7",
            disabled_bg="#132a3b",
            disabled_fg="#416885",
            padx=16,
            pady=7,
            state="disabled",
        )
        self._open_btn.pack(side="right", padx=(6, 0))

        self._open_docx_btn = StudioButton(
            action_bar,
            text="📝  Open Word Doc",
            command=self._open_docx_result,
            font=("Helvetica", 11, "bold"),
            bg="#4f46e5",
            fg="#ffffff",
            hover_bg="#4338ca",
            disabled_bg="#191d30",
            disabled_fg="#4b5075",
            padx=16,
            pady=7,
            state="disabled",
        )
        self._open_docx_btn.pack(side="right", padx=(6, 0))

        self._stop_btn = StudioButton(
            action_bar,
            text="⏹",
            command=self._stop_run,
            font=("Helvetica", 11, "bold"),
            bg="#2a1515",
            fg=ACCENT_RED_FG,
            hover_bg=ACCENT_RED_BG,
            disabled_bg="#151a24",
            disabled_fg="#354457",
            padx=12,
            pady=7,
            state="disabled",
        )
        self._stop_btn.pack(side="right", padx=(6, 0))

        # ── 3. Main Two-Column Workspace ───────────────────────────────
        workspace = tk.Frame(root, bg=BG_ROOT, padx=10, pady=4)
        workspace.grid(row=2, column=0, sticky="nsew")

        workspace.columnconfigure(0, weight=6)  # Left column ~60%
        workspace.columnconfigure(1, weight=4)  # Right column ~40%
        workspace.rowconfigure(0, weight=1)

        # ══════════════════════════════════════════════════════════════
        # LEFT COLUMN: Blocks List + Process Log Console
        # ══════════════════════════════════════════════════════════════
        left_col = tk.Frame(workspace, bg=BG_ROOT)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left_col.columnconfigure(0, weight=1)
        left_col.rowconfigure(0, weight=6)  # Blocks list
        left_col.rowconfigure(1, weight=4)  # Console log

        # Card A: Detected Blocks Card
        blocks_card = tk.Frame(left_col, bg=BG_CARD, highlightthickness=1, highlightbackground=BORDER_COLOR)
        blocks_card.grid(row=0, column=0, sticky="nsew", pady=(0, 5))
        blocks_card.columnconfigure(0, weight=1)
        blocks_card.rowconfigure(1, weight=1)

        # Card A Header
        b_hdr = tk.Frame(blocks_card, bg=BG_PANEL, padx=10, pady=7, highlightthickness=1, highlightbackground=BORDER_COLOR)
        b_hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(b_hdr, text="🔀  Detected Code & Chart Blocks", font=("Helvetica", 10, "bold"), fg=FG_WHITE, bg=BG_PANEL).pack(side="left")

        self._blocks_badge = tk.Label(
            b_hdr, text="0 Blocks Found", font=("Helvetica", 8, "bold"),
            fg=ACCENT_CYAN_FG, bg=ACCENT_CYAN_BG, padx=8, pady=2
        )
        self._blocks_badge.pack(side="left", padx=8)

        StudioButton(
            b_hdr, text="Deselect All", command=self._deselect_all_blocks,
            font=("Helvetica", 8), bg=BG_CARD_ALT, fg=FG_TEXT, hover_bg=BORDER_COLOR, padx=8, pady=2
        ).pack(side="right")

        StudioButton(
            b_hdr, text="Select All", command=self._select_all_blocks,
            font=("Helvetica", 8), bg=BG_CARD_ALT, fg=FG_TEXT, hover_bg=BORDER_COLOR, padx=8, pady=2
        ).pack(side="right", padx=6)

        # Scrollable area for Block Cards
        blocks_scroll_f = tk.Frame(blocks_card, bg=BG_CARD)
        blocks_scroll_f.grid(row=1, column=0, sticky="nsew")
        blocks_scroll_f.columnconfigure(0, weight=1)
        blocks_scroll_f.rowconfigure(0, weight=1)

        self._blocks_canvas = tk.Canvas(blocks_scroll_f, bg=BG_CARD, borderwidth=0, highlightthickness=0)
        self._blocks_scrollbar = ttk.Scrollbar(blocks_scroll_f, orient="vertical", command=self._blocks_canvas.yview, style="Vertical.TScrollbar")
        self._blocks_canvas.configure(yscrollcommand=self._blocks_scrollbar.set)

        self._blocks_scrollbar.pack(side="right", fill="y")
        self._blocks_canvas.pack(side="left", fill="both", expand=True)

        self._blocks_inner = tk.Frame(self._blocks_canvas, bg=BG_CARD, padx=6, pady=6)
        self._blocks_window_id = self._blocks_canvas.create_window((0, 0), window=self._blocks_inner, anchor="nw")

        self._blocks_canvas.bind("<Configure>", lambda e: self._blocks_canvas.itemconfig(self._blocks_window_id, width=e.width))
        self._blocks_inner.bind("<Configure>", lambda e: self._blocks_canvas.configure(scrollregion=self._blocks_canvas.bbox("all")))

        self._bind_mousewheel(self._blocks_canvas)
        self._bind_mousewheel(self._blocks_inner)

        self._blocks_placeholder = tk.Label(
            self._blocks_inner,
            text="  No file selected — drop a Python script or click Browse to detect execution blocks.",
            font=("Helvetica", 10),
            fg=FG_MUTED,
            bg=BG_CARD,
            pady=20,
        )
        self._blocks_placeholder.pack(anchor="w")

        # Card B: Process Log / Console Card
        console_card = tk.Frame(left_col, bg=BG_CARD, highlightthickness=1, highlightbackground=BORDER_COLOR)
        console_card.grid(row=1, column=0, sticky="nsew", pady=(5, 0))
        console_card.columnconfigure(0, weight=1)
        console_card.rowconfigure(1, weight=1)

        # Card B Header
        c_hdr = tk.Frame(console_card, bg=BG_PANEL, padx=10, pady=7, highlightthickness=1, highlightbackground=BORDER_COLOR)
        c_hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(c_hdr, text="⌨  Standard Output Stream & Process Log  ●", font=("Helvetica", 10, "bold"), fg=FG_WHITE, bg=BG_PANEL).pack(side="left")

        StudioButton(
            c_hdr, text="Clear", command=self._clear_console,
            font=("Helvetica", 8), bg=BG_CARD_ALT, fg=FG_TEXT, hover_bg=BORDER_COLOR, padx=8, pady=2
        ).pack(side="right")

        StudioButton(
            c_hdr, text="Copy", command=self._copy_console,
            font=("Helvetica", 8), bg=BG_CARD_ALT, fg=FG_TEXT, hover_bg=BORDER_COLOR, padx=8, pady=2
        ).pack(side="right", padx=6)

        # Console Text Box
        c_box = tk.Frame(console_card, bg=BG_CODE_BOX)
        c_box.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)

        self._console = tk.Text(
            c_box,
            height=8,
            wrap="word",
            bg=BG_CODE_BOX,
            fg="#d4d4d4",
            insertbackground=FG_WHITE,
            font=("Menlo", 10) if platform.system() == "Darwin" else ("Courier New", 10),
            state="disabled",
            relief="flat",
            bd=0,
            padx=8,
            pady=6,
        )
        c_sb = ttk.Scrollbar(c_box, orient="vertical", command=self._console.yview, style="Vertical.TScrollbar")
        self._console.configure(yscrollcommand=c_sb.set)
        c_sb.pack(side="right", fill="y")
        self._console.pack(side="left", fill="both", expand=True)

        # Tags
        self._console.tag_config("ok", foreground=ACCENT_GREEN_FG)
        self._console.tag_config("err", foreground=ACCENT_RED_FG)
        self._console.tag_config("info", foreground=ACCENT_CYAN_FG)
        self._console.tag_config("skip", foreground=ACCENT_AMBER_FG)
        self._console.tag_config("dim", foreground=FG_DIM)

        # ══════════════════════════════════════════════════════════════
        # RIGHT COLUMN: Latest Block Preview + PDF Report Card
        # ══════════════════════════════════════════════════════════════
        right_col = tk.Frame(workspace, bg=BG_ROOT)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_col.columnconfigure(0, weight=1)
        right_col.rowconfigure(0, weight=8)  # Preview
        right_col.rowconfigure(1, weight=2)  # PDF Card

        # Card C: Latest Block Preview Card
        prev_card = tk.Frame(right_col, bg=BG_CARD, highlightthickness=1, highlightbackground=BORDER_COLOR)
        prev_card.grid(row=0, column=0, sticky="nsew", pady=(0, 5))
        prev_card.columnconfigure(0, weight=1)
        prev_card.rowconfigure(2, weight=1)

        # Card C Header
        p_hdr = tk.Frame(prev_card, bg=BG_PANEL, padx=10, pady=7, highlightthickness=1, highlightbackground=BORDER_COLOR)
        p_hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(p_hdr, text="👁  Latest Block Preview", font=("Helvetica", 10, "bold"), fg=FG_WHITE, bg=BG_PANEL).pack(side="left")

        tk.Label(
            p_hdr, text="Terminal / Chart Proof", font=("Helvetica", 8, "bold"),
            fg=ACCENT_CYAN_FG, bg=ACCENT_CYAN_BG, padx=8, pady=2
        ).pack(side="right")

        # Sub-header bar with block title & anti-aliased badge
        self._prev_sub_hdr = tk.Frame(prev_card, bg="#0e1522", padx=10, pady=4, highlightthickness=1, highlightbackground=BORDER_COLOR)
        self._prev_sub_hdr.grid(row=1, column=0, sticky="ew")
        self._prev_block_title_lbl = tk.Label(
            self._prev_sub_hdr, text="●  Ready — Waiting for execution",
            font=("Helvetica", 9, "bold"), fg=ACCENT_CYAN_FG, bg="#0e1522"
        )
        self._prev_block_title_lbl.pack(side="left")
        tk.Label(
            self._prev_sub_hdr, text="300 DPI • Anti-Aliased",
            font=("Helvetica", 8), fg=FG_MUTED, bg="#0e1522"
        ).pack(side="right")

        # Preview Display Container
        self._prev_canvas_f = tk.Frame(prev_card, bg=BG_CODE_BOX)
        self._prev_canvas_f.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)
        self._prev_canvas_f.columnconfigure(0, weight=1)
        self._prev_canvas_f.rowconfigure(0, weight=1)

        self._preview_label = tk.Label(
            self._prev_canvas_f,
            text="⚡  Awaiting Execution\n\nRun the script or click any executed block in the left panel\nto display high-resolution terminal output / chart preview.",
            font=("Helvetica", 10),
            fg=FG_MUTED,
            bg=BG_CODE_BOX,
            justify="center",
        )
        self._preview_label.grid(row=0, column=0, sticky="nsew")

        # Card C Footer
        p_ftr = tk.Frame(prev_card, bg="#0e1522", padx=10, pady=4, highlightthickness=1, highlightbackground=BORDER_COLOR)
        p_ftr.grid(row=3, column=0, sticky="ew")
        self._prev_latency_lbl = tk.Label(p_ftr, text="⚡  Latency: --", font=("Helvetica", 8), fg=FG_MUTED, bg="#0e1522")
        self._prev_latency_lbl.pack(side="left")
        self._prev_dim_lbl = tk.Label(p_ftr, text="📐 Dimensions: --", font=("Helvetica", 8), fg=FG_MUTED, bg="#0e1522")
        self._prev_dim_lbl.pack(side="right")

        # Card D: PDF Report Artifact Card
        pdf_card = tk.Frame(right_col, bg=BG_CARD, padx=12, pady=10, highlightthickness=1, highlightbackground=BORDER_COLOR)
        pdf_card.grid(row=1, column=0, sticky="ew", pady=(5, 0))

        pdf_left = tk.Frame(pdf_card, bg=BG_CARD)
        pdf_left.pack(side="left", fill="both", expand=True)

        pdf_icon_box = tk.Label(pdf_left, text="📕", font=("Helvetica", 20), bg=BG_CARD)
        pdf_icon_box.pack(side="left", padx=(0, 10))

        pdf_text_box = tk.Frame(pdf_left, bg=BG_CARD)
        pdf_text_box.pack(side="left", fill="y", anchor="w")

        self._pdf_name_lbl = tk.Label(
            pdf_text_box,
            text="report_dossier.pdf  ○",
            font=("Helvetica", 10, "bold"),
            fg=FG_WHITE,
            bg=BG_CARD,
        )
        self._pdf_name_lbl.pack(anchor="w")

        self._pdf_details_lbl = tk.Label(
            pdf_text_box,
            text="Status: Pending compilation",
            font=("Helvetica", 9),
            fg=FG_MUTED,
            bg=BG_CARD,
        )
        self._pdf_details_lbl.pack(anchor="w")

        self._card_open_docx_btn = StudioButton(
            pdf_card,
            text="📝  DOCX",
            command=self._open_docx_result,
            font=("Helvetica", 9, "bold"),
            bg="#1c2538",
            fg=FG_MUTED,
            hover_bg=BORDER_COLOR,
            disabled_bg="#131924",
            disabled_fg="#354457",
            padx=10,
            pady=6,
            state="disabled",
        )
        self._card_open_docx_btn.pack(side="right")

        self._card_open_btn = StudioButton(
            pdf_card,
            text="👁  PDF Proof",
            command=self._open_result,
            font=("Helvetica", 9, "bold"),
            bg="#1c2538",
            fg=FG_MUTED,
            hover_bg=BORDER_COLOR,
            disabled_bg="#131924",
            disabled_fg="#354457",
            padx=10,
            pady=6,
            state="disabled",
        )
        self._card_open_btn.pack(side="right", padx=(0, 4))

    # ------------------------------------------------------------------
    # Mousewheel Helper
    # ------------------------------------------------------------------

    def _bind_mousewheel(self, widget: tk.Widget) -> None:
        def _wheel(e):
            if platform.system() == "Darwin":
                delta = -1 * e.delta
            elif e.num == 4:
                delta = -1
            elif e.num == 5:
                delta = 1
            else:
                delta = -1 * (e.delta // 120)
            self._blocks_canvas.yview_scroll(delta, "units")
            return "break"

        widget.bind("<MouseWheel>", _wheel, add="+")
        widget.bind("<Button-4>", _wheel, add="+")
        widget.bind("<Button-5>", _wheel, add="+")

    def _toggle_config_panel(self) -> None:
        if self._config_expanded:
            self._config_body.pack_forget()
            self._config_expanded = False
            self._toggle_config_btn.config(text="▼  Expand Config")
            self._toggle_hdr_btn.config(text="▶  Execution Context & Report Generator Configuration")
        else:
            self._config_body.pack(fill="x")
            self._config_expanded = True
            self._toggle_config_btn.config(text="▲  Collapse Config")
            self._toggle_hdr_btn.config(text="▼  Execution Context & Report Generator Configuration")

    # ------------------------------------------------------------------
    # File selection / block detection
    # ------------------------------------------------------------------

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Python Script",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if path:
            self._script_var.set(path)
            self._on_file_selected()

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save PDF as…",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if path:
            self._outpath_var.set(path)

    def _open_font_chooser(self) -> None:
        def on_select(font_name: str):
            self._font_var.set(font_name)
            self._font_display_var.set(font_name if font_name else "Iosevka NF / Sys")

        FontChooserDialog(self.root, self._font_var.get(), on_select)

    def _setup_drag_and_drop(self) -> None:
        """Enable drag-and-drop file loading across platforms."""
        if _HAS_TKDND:
            self._register_drop_target(self.root)

        if platform.system() == "Darwin":
            try:
                self.root.createcommand("::tk::mac::OpenDocument", self._on_mac_open_document)
            except Exception:
                pass

    def _register_drop_target(self, widget: tk.Widget) -> None:
        if not _HAS_TKDND:
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_dnd_drop)
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                self._register_drop_target(child)
        except Exception:
            pass

    def _on_dnd_drop(self, event) -> str:
        if not event or not getattr(event, "data", None):
            return "break"
        data = event.data
        candidates = [str(data)]
        try:
            candidates.extend(self.root.tk.splitlist(data))
        except Exception:
            pass
        candidates.extend(str(data).splitlines())

        for raw_item in candidates:
            clean = _clean_dropped_path(raw_item)
            if clean and Path(clean).exists() and Path(clean).is_file():
                self._script_var.set(clean)
                self._on_file_selected()
                return "break"
        return "break"

    def _on_mac_open_document(self, *paths) -> None:
        for p in paths:
            clean = _clean_dropped_path(str(p))
            if clean and Path(clean).exists() and Path(clean).is_file():
                self._script_var.set(clean)
                self._on_file_selected()
                break

    def _check_auto_parse(self) -> None:
        raw = self._script_var.get()
        clean = _clean_dropped_path(raw)
        if clean and Path(clean).exists() and Path(clean).is_file():
            if clean != raw:
                self._script_var.set(clean)
            self._on_file_selected()

    def _on_file_selected(self) -> None:
        raw = self._script_var.get()
        script = _clean_dropped_path(raw)
        if script != raw:
            self._script_var.set(script)
        if not script or not Path(script).exists() or not Path(script).is_file():
            self._ast_pill.config(text="FILE ERROR", bg="#3b1111", fg=ACCENT_RED_FG)
            return

        p = Path(script)
        if p.suffix.lower() == ".pdf":
            self._ast_pill.config(text="PDF DOCUMENT", bg="#3b2b11", fg=ACCENT_AMBER_FG)
            self._outpath_var.set(str(p))
            self._pdf_name_lbl.config(text=f"{p.name}  ●")
            self._log(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [INFO] '{p.name}' is an existing PDF document. Please select a Python script (.py) to execute blocks and generate PDF reports.\n", "info")
            return

        self._ast_pill.config(text="PY AST OK", bg=ACCENT_GREEN_BG, fg=ACCENT_GREEN_FG)

        current_out = self._outpath_var.get().strip()
        if not current_out or current_out.endswith("_output.pdf") or "report_dossier.pdf" in current_out:
            default_out = str(p.parent / f"{p.stem}_output.pdf")
            self._outpath_var.set(default_out)
            self._pdf_name_lbl.config(text=f"{Path(default_out).name}  ○")

        # Parse blocks
        try:
            self._blocks = parse_file(script)
            self._refresh_block_list()
            self._log(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [SUCCESS] Script parsed: {len(self._blocks)} execution block(s) detected.\n", "ok")
        except Exception as exc:
            self._log(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [ERROR] Parsing failed: {exc}\n", "err")

    # ------------------------------------------------------------------
    # Block List Card Generation
    # ------------------------------------------------------------------

    def _refresh_block_list(self) -> None:
        """Rebuild the detected-blocks cards inside the scrollable checklist."""
        for widget in self._blocks_inner.winfo_children():
            widget.destroy()
        self._block_vars.clear()
        self._block_card_widgets.clear()

        if not self._blocks:
            lbl = tk.Label(
                self._blocks_inner,
                text="  No blocks detected in this script.",
                font=("Helvetica", 10),
                fg=FG_MUTED,
                bg=BG_CARD,
                pady=20,
            )
            lbl.pack(anchor="w")
            self._blocks_badge.config(text="0 Blocks Found")
            self._blocks_stat_pill.config(text="● Blocks: 0 / 0 Active")
            return

        self._blocks_badge.config(text=f"{len(self._blocks)} Blocks Found")
        active_cnt = sum(1 for b in self._blocks if not b.skip)
        self._blocks_stat_pill.config(text=f"● Blocks: {active_cnt} / {len(self._blocks)} Active")

        for b in self._blocks:
            var = tk.BooleanVar(value=not b.skip)
            self._block_vars.append(var)

            # Metadata computation
            idx_str = f"{b.index:02d}"
            loc_lines = [ln for ln in b.code.splitlines() if ln.strip()]
            loc_count = len(loc_lines)
            if not b.code.strip():
                type_str = "Section Header"
                first_code = "(Section Header • Title Only)"
            else:
                is_chart = any(k in b.code for k in ("plt.", "matplotlib", "sns.", "plot(", "figure("))
                type_str = "Matplotlib / Chart Plot" if is_chart else "Python Script"

                first_code = next(
                    (ln.strip() for ln in b.code.splitlines() if ln.strip() and not ln.strip().startswith("#")),
                    "(empty block)"
                )

            # Card Container
            card = tk.Frame(
                self._blocks_inner,
                bg=BG_CARD_ALT,
                highlightthickness=1,
                highlightbackground=BORDER_COLOR,
                padx=10,
                pady=7,
                cursor="hand2",
            )
            card.pack(fill="x", pady=3)

            # Top Row: Checkbox + Title + Status Pill
            top_r = tk.Frame(card, bg=BG_CARD_ALT)
            top_r.pack(fill="x")

            cb = BlockCheckbox(
                top_r,
                variable=var,
                command=lambda b_idx=b.index-1: self._on_checkbox_toggled(b_idx),
            )
            cb.pack(side="left")

            title_lbl = tk.Label(
                top_r,
                text=f" {idx_str}  {b.title}",
                font=("Helvetica", 10, "bold"),
                fg=FG_WHITE,
                bg=BG_CARD_ALT,
                cursor="hand2",
            )
            title_lbl.pack(side="left", padx=4)

            pill_lbl = tk.Label(
                top_r,
                text="● Active" if not b.skip else "● Skipped",
                font=("Helvetica", 8, "bold"),
                fg=ACCENT_GREEN_FG if not b.skip else ACCENT_AMBER_FG,
                bg=ACCENT_GREEN_BG if not b.skip else ACCENT_AMBER_BG,
                padx=6,
                pady=1,
            )
            pill_lbl.pack(side="right")

            # Subtitle Line
            sub_r = tk.Frame(card, bg=BG_CARD_ALT)
            sub_r.pack(fill="x", pady=(2, 3))
            sub_lbl = tk.Label(
                sub_r,
                text=f"{{ }} Type: {type_str}  •  Lines: {b.start_line} - {b.end_line} ({loc_count} LOC)",
                font=("Helvetica", 8),
                fg=FG_MUTED,
                bg=BG_CARD_ALT,
                cursor="hand2",
            )
            sub_lbl.pack(side="left", padx=(24, 0))

            # Code Snippet Box
            code_f = tk.Frame(card, bg=BG_CODE_BOX, highlightthickness=1, highlightbackground="#1b2434", padx=6, pady=2)
            code_f.pack(fill="x", padx=(24, 0))
            code_lbl = tk.Label(
                code_f,
                text=first_code[:85],
                font=("Menlo", 9) if platform.system() == "Darwin" else ("Courier New", 9),
                fg=FG_MUTED,
                bg=BG_CODE_BOX,
                anchor="w",
                cursor="hand2",
            )
            code_lbl.pack(fill="x")

            # Click binding to inspect preview
            card_info = {
                "card": card,
                "pill": pill_lbl,
                "block": b,
                "index": b.index,
            }
            self._block_card_widgets.append(card_info)

            for w in (card, title_lbl, sub_r, sub_lbl, code_f, code_lbl):
                w.bind("<Button-1>", lambda e, b_idx=b.index: self._select_card_for_preview(b_idx))
                self._bind_mousewheel(w)

        self._register_drop_target(self._blocks_inner)
        self.root.after_idle(lambda: self._blocks_canvas.configure(scrollregion=self._blocks_canvas.bbox("all")))

    def _on_checkbox_toggled(self, b_idx: int) -> None:
        if 0 <= b_idx < len(self._block_vars) and b_idx < len(self._block_card_widgets):
            is_active = self._block_vars[b_idx].get()
            self._blocks[b_idx].skip = not is_active
            pill = self._block_card_widgets[b_idx]["pill"]
            if is_active:
                pill.config(text="● Active", fg=ACCENT_GREEN_FG, bg=ACCENT_GREEN_BG)
            else:
                pill.config(text="● Skipped", fg=ACCENT_AMBER_FG, bg=ACCENT_AMBER_BG)

            active_cnt = sum(1 for v in self._block_vars if v.get())
            self._blocks_stat_pill.config(text=f"● Blocks: {active_cnt} / {len(self._block_vars)} Active")

    def _select_all_blocks(self) -> None:
        for i, var in enumerate(self._block_vars):
            var.set(True)
            self._blocks[i].skip = False
            if i < len(self._block_card_widgets):
                self._block_card_widgets[i]["pill"].config(text="● Active", fg=ACCENT_GREEN_FG, bg=ACCENT_GREEN_BG)
        self._blocks_stat_pill.config(text=f"● Blocks: {len(self._block_vars)} / {len(self._block_vars)} Active")

    def _deselect_all_blocks(self) -> None:
        for i, var in enumerate(self._block_vars):
            var.set(False)
            self._blocks[i].skip = True
            if i < len(self._block_card_widgets):
                self._block_card_widgets[i]["pill"].config(text="● Skipped", fg=ACCENT_AMBER_FG, bg=ACCENT_AMBER_BG)
        self._blocks_stat_pill.config(text=f"● Blocks: 0 / {len(self._block_vars)} Active")

    def _select_card_for_preview(self, block_index: int) -> None:
        """User clicked a block card — highlight it and show its preview if available."""
        self._selected_block_idx = block_index
        for info in self._block_card_widgets:
            if info["index"] == block_index:
                info["card"].config(bg=BG_CARD_ACTIVE, highlightbackground=BORDER_ACTIVE)
            else:
                info["card"].config(bg=BG_CARD_ALT, highlightbackground=BORDER_COLOR)

        target_block = next((b for b in self._blocks if b.index == block_index), None)
        if target_block:
            self._prev_block_title_lbl.config(text=f"●  Block {target_block.index:02d}: {target_block.title}")

        # If we have execution results for this block, display immediately
        matching_res = next((r for r in self._results if r.index == block_index), None)
        if matching_res:
            self._render_and_preview(matching_res)

    def _clear_console(self) -> None:
        self._console.config(state="normal")
        self._console.delete("1.0", "end")
        self._console.config(state="disabled")

    def _copy_console(self) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._console.get("1.0", "end-1c"))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _start_run(self) -> None:
        script = self._script_var.get().strip()
        if not script:
            messagebox.showerror("No Script", "Please select or drop a Python script first.")
            return
        if not Path(script).exists():
            messagebox.showerror("File Not Found", f"File not found:\n{script}")
            return
        if self._running:
            return

        self._running = True
        self._results = []
        self._out_path = None
        self._docx_path = None

        self._run_btn.set_state("disabled")
        self._open_btn.set_state("disabled")
        self._open_docx_btn.set_state("disabled")
        self._card_open_btn.set_state("disabled")
        if hasattr(self, "_card_open_docx_btn"):
            self._card_open_docx_btn.set_state("disabled")
        self._stop_btn.set_state("normal")

        self._clear_console()
        self._status_badge.config(text="⏳  Execution in Progress...", fg=ACCENT_AMBER_FG, bg=ACCENT_AMBER_BG)

        isolated     = self._state_var.get() == "Isolated"
        theme        = self._theme_var.get()
        show_code    = self._code_var.get()
        no_cover     = not self._cover_var.get()
        timeout      = max(0, int(self._timeout_var.get() or "30"))
        out_path_str = self._outpath_var.get().strip()
        out_path     = Path(out_path_str) if out_path_str else None
        author_name  = self._name_var.get().strip() or None
        author_id    = self._id_var.get().strip() or None

        from .renderer import resolve_font_path as _rfp
        raw_font = self._font_var.get().strip()
        font_path = _rfp(raw_font) if raw_font else None
        docx_enabled = bool(getattr(self, "_docx_var", None) and self._docx_var.get() and is_docx_available())

        try:
            blocks = parse_file(script)
        except Exception as exc:
            messagebox.showerror("Parse Error", str(exc))
            self._running = False
            self._run_btn.set_state("normal")
            return

        for i, (block, var) in enumerate(zip(blocks, self._block_vars)):
            block.skip = not var.get()

        active_count = sum(1 for b in blocks if not b.skip)
        self._done_count = 0
        self._blocks_stat_pill.config(text=f"● Blocks: {active_count} / {len(blocks)} Active")

        ts_now = datetime.datetime.now().strftime("%H:%M:%S")
        self._log(f"[{ts_now}] [INFO] Starting execution pipeline ({active_count} active blocks)...\n", "info")

        def _worker():
            def callback(idx, event, data=None):
                self._q.put((idx, event, data))

            def input_callback(prompt: str, b_idx: int = 0, b_title: str = "") -> str:
                evt = threading.Event()
                res = [""]
                self._q.put((b_idx, "request_input", {
                    "prompt": prompt,
                    "block_index": b_idx,
                    "block_title": b_title,
                    "event": evt,
                    "result": res,
                }))
                evt.wait()
                return res[0]

            source_text = Path(script).read_text(encoding="utf-8", errors="replace")
            executor = Executor(
                isolated=isolated,
                timeout=float(timeout),
                progress_callback=callback,
                full_source=source_text,
                input_handler=input_callback,
            )
            results = executor.run(blocks)

            if out_path is None:
                p = Path(script)
                pdf_path = p.parent / f"{p.stem}_output.pdf"
            else:
                pdf_path = out_path

            docx_path = pdf_path.with_suffix(".docx")

            try:
                final_path = build_pdf(
                    results=results,
                    output_path=pdf_path,
                    script_path=script,
                    script_name=Path(script).name,
                    theme=theme,
                    show_code=show_code,
                    author_name=author_name,
                    author_id=author_id,
                    no_cover=no_cover,
                    font_path=font_path,
                )
                self._q.put((0, "pdf_done", final_path))
            except Exception as exc:
                self._q.put((0, "pdf_error", str(exc)))

            if docx_enabled:
                try:
                    final_docx_path = build_docx(
                        results=results,
                        output_path=docx_path,
                        script_path=script,
                        script_name=Path(script).name,
                        theme=theme,
                        show_code=show_code,
                        author_name=author_name,
                        author_id=author_id,
                        no_cover=no_cover,
                        font_path=font_path,
                    )
                    self._q.put((0, "docx_done", final_docx_path))
                except Exception as exc:
                    self._q.put((0, "docx_error", str(exc)))

            self._q.put((0, "all_done", results))

        threading.Thread(target=_worker, daemon=True).start()

    def _stop_run(self) -> None:
        self._log(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [CANCEL] Run cancel requested.\n", "skip")
        self._running = False
        self._run_btn.set_state("normal")
        self._stop_btn.set_state("disabled")

    # ------------------------------------------------------------------
    # Event Queue Polling
    # ------------------------------------------------------------------

    def _poll_queue(self) -> None:
        try:
            processed = 0
            while processed < 100:
                idx, event, data = self._q.get_nowait()
                self._handle_event(idx, event, data)
                processed += 1
        except queue.Empty:
            pass
        finally:
            self.root.after(POLL_MS, self._poll_queue)

    def _handle_event(self, idx: int, event: str, data) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")

        if event == "start":
            self._status_badge.config(text=f"⏳ Running: {data}…", fg=ACCENT_AMBER_FG, bg=ACCENT_AMBER_BG)
            self._log(f"[{ts}] [RUN] ▶ {data}\n", "info")

        elif event == "stdout":
            self._log(data)

        elif event == "stderr":
            self._log(data, "err")

        elif event == "skip":
            self._log(f"[{ts}] [SKIP] ⊘ {data} (skipped)\n", "skip")

        elif event == "request_input":
            prompt = data["prompt"]
            b_idx = data["block_index"]
            b_title = data["block_title"]
            evt = data["event"]
            res = data["result"]

            display_prompt = prompt.strip() or "Input required"
            self._status_badge.config(text=f"⌨ Awaiting Input: {display_prompt}", fg=ACCENT_CYAN_FG, bg=ACCENT_CYAN_BG)
            self._log(f"[{ts}] [INPUT] Waiting for user input: {display_prompt}…\n", "info")

            def _on_done(val: str):
                res[0] = val
                evt.set()
                self._log(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [INPUT] → Received: {val}\n", "ok")

            InputDialog(
                parent=self.root,
                prompt=prompt,
                block_title=b_title,
                block_index=b_idx,
                theme=self._theme_var.get(),
                on_submit=_on_done,
            )

        elif event == "done" and isinstance(data, dict):
            self._done_count += 1
            ok = data["success"]
            ms = data["duration_ms"]
            time_str = f"{ms/1000:.2f}s" if ms >= 1000 else f"{ms:.0f}ms"
            figs = data.get("figures", 0)
            fig_note = f"  [{figs} plot(s)]" if figs else ""
            sym = "✓" if ok else "✗"
            tag = "ok" if ok else "err"
            self._log(f"[{ts}] [BLOCK] {sym} Finished in {time_str}{fig_note}\n", tag)

        elif event == "preview":
            self._update_preview(data)

        elif event == "pdf_done":
            self._out_path = Path(data)
            self._log(f"[{ts}] [PDF] ✓ Dossier saved → {data}\n", "ok")
            self._open_btn.set_state("normal")
            self._card_open_btn.set_state("normal")

            # Update PDF Card
            if self._out_path.exists():
                size_mb = self._out_path.stat().st_size / (1024 * 1024)
                self._pdf_name_lbl.config(text=f"{self._out_path.name}  ●", fg=FG_WHITE)
                docx_note = " & .docx" if (self._docx_path and self._docx_path.exists()) else ""
                self._pdf_details_lbl.config(text=f"Target: Compiled .pdf{docx_note} • {size_mb:.2f} MB", fg=ACCENT_GREEN_FG)

        elif event == "pdf_error":
            self._log(f"[{ts}] [ERROR] PDF build failed: {data}\n", "err")
            self._status_badge.config(text="✗ PDF Build Failed", fg=ACCENT_RED_FG, bg=ACCENT_RED_BG)

        elif event == "docx_done":
            self._docx_path = Path(data)
            self._log(f"[{ts}] [DOCX] ✓ Word Document saved → {data}\n", "ok")
            self._open_docx_btn.set_state("normal")
            if hasattr(self, "_card_open_docx_btn"):
                self._card_open_docx_btn.set_state("normal")
            if self._out_path and self._out_path.exists():
                size_mb = self._out_path.stat().st_size / (1024 * 1024)
                self._pdf_details_lbl.config(text=f"Target: Compiled .pdf & .docx • {size_mb:.2f} MB", fg=ACCENT_GREEN_FG)

        elif event == "docx_error":
            self._log(f"[{ts}] [ERROR] Word document build failed: {data}\n", "err")

        elif event == "all_done":
            self._results = data
            self._running = False
            self._run_btn.set_state("normal")
            self._stop_btn.set_state("disabled")

            total_ms = sum(r.duration_ms for r in data if not r.skipped)
            ts_str = f"{total_ms/1000:.2f}s"
            self._duration_lbl.config(text=f"⏱  Duration: {ts_str}")
            self._status_badge.config(text=f"✓  Execution Complete ({ts_str})", fg=ACCENT_GREEN_FG, bg=ACCENT_GREEN_BG)

            last = next((r for r in reversed(data) if not r.skipped), None)
            if last:
                self._select_card_for_preview(last.index)

    # ------------------------------------------------------------------
    # Console & Preview Helpers
    # ------------------------------------------------------------------

    def _log(self, text: str, tag: str = "") -> None:
        self._console.config(state="normal")
        if tag:
            self._console.insert("end", text, tag)
        else:
            self._console.insert("end", text)
        try:
            line_count = int(self._console.index("end-1c").split(".")[0])
            if line_count > 5000:
                self._console.delete("1.0", "1000.0")
        except Exception:
            pass
        self._console.see("end")
        self._console.config(state="disabled")

    def _render_and_preview(self, result: BlockResult) -> None:
        """Render the terminal output or plot and update preview panel in a background thread."""
        script = self._script_var.get().strip()
        raw_font = self._font_var.get().strip()
        from .renderer import resolve_font_path as _rfp
        font_path = _rfp(raw_font) if raw_font else None

        self._prev_block_title_lbl.config(
            text=f"● Block {result.index:02d}: {result.title}  •  {result.duration_ms:.0f}ms"
        )
        self._prev_latency_lbl.config(text=f"⚡ Latency: {result.duration_ms:.0f}ms")

        # If block generated a figure, preview the first plot!
        if result.figures:
            fig = result.figures[0].copy()
            w, h = fig.size
            self._prev_dim_lbl.config(text=f"📐 Plot: {w} × {h} px")
            fig.thumbnail((PREVIEW_MAX_W, PREVIEW_MAX_H), Image.LANCZOS)
            self._q.put((0, "preview", fig))
            return

        # Otherwise render terminal output
        def _render():
            from .renderer import render_terminal_output
            img = render_terminal_output(
                result.stdout,
                result.stderr,
                width_px=PREVIEW_MAX_W * 2,
                font_path=font_path,
                show_prompt=True,
                script_path=script,
                script_name=Path(script).name if script else "script.py",
                duration_ms=result.duration_ms,
            )
            w, h = img.size
            self.root.after_idle(lambda: self._prev_dim_lbl.config(text=f"📐 Dimensions: {w} × {h} px"))
            img.thumbnail((PREVIEW_MAX_W, PREVIEW_MAX_H), Image.LANCZOS)
            self._q.put((0, "preview", img))

        threading.Thread(target=_render, daemon=True).start()

    def _update_preview(self, img: Image.Image) -> None:
        photo = ImageTk.PhotoImage(img)
        self._preview_photo = photo
        self._preview_label.config(image=photo, text="")

    def _open_result(self) -> None:
        if self._out_path and self._out_path.exists():
            _open_file(self._out_path)

    def _open_docx_result(self) -> None:
        if self._docx_path and self._docx_path.exists():
            _open_file(self._docx_path)


# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------

def launch_gui(args: Optional[argparse.Namespace] = None) -> None:
    """Create the Tk root window and start the main loop."""
    if _HAS_TKDND:
        try:
            root = TkinterDnD.Tk()
        except Exception:
            root = tk.Tk()
    else:
        root = tk.Tk()

    try:
        root.tk.call("::tk::mac::standardAboutPanel")
    except Exception:
        pass

    prefill = None
    if args and getattr(args, "script", None):
        prefill = args.script

    app = PyBlockRunnerApp(root, prefill_script=prefill)

    # Apply pre-filled flags from CLI
    if args:
        if getattr(args, "name", None):
            app._name_var.set(args.name)
        if getattr(args, "id", None):
            app._id_var.set(args.id)
        if getattr(args, "theme", None):
            app._theme_var.set(args.theme)
        if getattr(args, "isolated", False):
            app._state_var.set("Isolated")
        if getattr(args, "code", False):
            app._code_var.set(True)
        elif getattr(args, "no_code", False):
            app._code_var.set(False)
        if getattr(args, "cover", False):
            app._cover_var.set(True)
        elif getattr(args, "no_cover", False):
            app._cover_var.set(False)
        if getattr(args, "timeout", None):
            app._timeout_var.set(str(args.timeout))
        if getattr(args, "output", None):
            app._outpath_var.set(args.output)

    root.mainloop()
