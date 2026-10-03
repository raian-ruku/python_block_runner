"""
gui.py - Native Desktop GUI for PyBlockRunner

A Tkinter-based desktop application window.

Layout:
  ┌─────────────────────────────────────────────────────────┐
  │  File:  [___________________________]  [Browse]  [Run]  │
  │  Name:  [___________]  ID: [___________]  Theme: [dark▼]│
  │  State: [Shared▼]   Include Code: [✓]   Timeout: [30s]  │
  ├─────────────────────────────────────────────────────────┤
  │  Detected Blocks:                                       │
  │  [✓] 1. Problem 1: Imports & Setup                      │
  │  [✓] 2. Problem 2: Data Processing                      │
  │  ...                                                    │
  ├─────────────────────────────────────────────────────────┤
  │  Progress:  ████████░░░░░  Block 2 of 5                 │
  │                                                         │
  │  [Live Console Output stream ...]                       │
  │                                                         │
  ├─────────────────────────────────────────────────────────┤
  │  Latest Screenshot Preview:                             │
  │  [thumbnail]                                            │
  └─────────────────────────────────────────────────────────┘

Thread safety: all execution runs in a daemon background thread.
GUI updates come only via root.after() calls, never directly from the thread.
"""

from __future__ import annotations

import argparse
import os
import platform
import queue
import subprocess
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageTk

from .parser import parse_file
from .executor import Executor, BlockResult
from .pdf_builder import build_pdf


# ---------------------------------------------------------------------------
# Font Chooser Modal Dialog
# ---------------------------------------------------------------------------

class FontChooserDialog(tk.Toplevel):
    """Interactive font chooser dialog listing all installed system fonts with live preview."""

    def __init__(self, parent: tk.Tk, current_font: str, on_select):
        super().__init__(parent)
        self.title("Select Document Font")
        self.geometry("460x520")
        self.minsize(400, 420)
        self.transient(parent)
        self.grab_set()

        self._on_select = on_select
        try:
            raw_fonts = sorted(set(tkfont.families()))
        except Exception:
            raw_fonts = []
        self._all_fonts = [f for f in raw_fonts if not f.startswith("@")]

        # Search bar
        top_f = ttk.Frame(self, padding=10)
        top_f.pack(fill="x")
        ttk.Label(top_f, text="Search:").pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._filter_fonts)
        search_entry = ttk.Entry(top_f, textvariable=self._search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=6)
        search_entry.focus_set()

        # Font listbox
        list_f = ttk.Frame(self, padding=(10, 0, 10, 6))
        list_f.pack(fill="both", expand=True)
        self._listbox = tk.Listbox(list_f, selectmode="single", exportselection=False, font=("TkDefaultFont", 11))
        scrollbar = ttk.Scrollbar(list_f, orient="vertical", command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=scrollbar.set)
        self._listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._listbox.bind("<<ListboxSelect>>", self._on_select_item)
        self._listbox.bind("<Double-Button-1>", lambda e: self._choose())

        # Preview frame
        prev_f = ttk.LabelFrame(self, text="Font Preview", padding=8)
        prev_f.pack(fill="x", padx=10, pady=4)
        self._preview_lbl = tk.Label(
            prev_f,
            text="The quick brown fox jumps over the lazy dog 12345",
            font=("TkDefaultFont", 12),
            wraplength=420,
            justify="center",
        )
        self._preview_lbl.pack(fill="x", pady=4)

        # Bottom actions
        btn_f = ttk.Frame(self, padding=10)
        btn_f.pack(fill="x")
        if platform.system() == "Darwin":
            ttk.Button(btn_f, text="Open Font Book", command=self._open_font_book).pack(side="left")

        ttk.Button(btn_f, text="Reset Default", command=self._reset_default).pack(side="left", padx=4)
        ttk.Button(btn_f, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btn_f, text="Select", command=self._choose).pack(side="right", padx=4)

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
        self.transient(parent)
        self.resizable(False, False)
        self._on_submit = on_submit
        self._submitted = False

        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        header_text = f"Block {block_index}" if block_index > 0 else "Script"
        if block_title:
            header_text += f": {block_title}"

        lbl_header = ttk.Label(
            container,
            text=f"⌨  {header_text}",
            font=("Helvetica", 12, "bold"),
        )
        lbl_header.pack(anchor="w", pady=(0, 6))

        clean_prompt = prompt.rstrip("\r\n") if prompt else "Please enter value:"
        lbl_prompt = ttk.Label(
            container,
            text=clean_prompt,
            font=("Helvetica", 11),
            wraplength=420,
            justify="left",
        )
        lbl_prompt.pack(anchor="w", pady=(0, 10))

        self._entry_var = tk.StringVar()
        self._entry = ttk.Entry(
            container,
            textvariable=self._entry_var,
            width=46,
            font=("Helvetica", 11),
        )
        self._entry.pack(fill="x", pady=(0, 16))
        self._entry.bind("<Return>", lambda _: self._submit())
        self._entry.bind("<Escape>", lambda _: self._cancel())

        btn_box = ttk.Frame(container)
        btn_box.pack(fill="x")

        ttk.Button(btn_box, text="Cancel", command=self._cancel).pack(side="right", padx=(6, 0))
        submit_btn = ttk.Button(btn_box, text="Submit", command=self._submit)
        submit_btn.pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._cancel)

        # Center over parent and lift above other windows
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        w = max(460, self.winfo_reqwidth())
        h = max(160, self.winfo_reqheight())
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.lift()
        try:
            self.attributes("-topmost", True)
            self.after_idle(lambda: self.attributes("-topmost", False))
        except Exception:
            pass

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
# Constants
# ---------------------------------------------------------------------------

PREVIEW_MAX_W = 500
PREVIEW_MAX_H = 320
POLL_MS = 80   # How often to drain the event queue (milliseconds)


# ---------------------------------------------------------------------------
# Helper: open PDF in system viewer
# ---------------------------------------------------------------------------

def _open_pdf(path: Path) -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif platform.system() == "Windows":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main GUI Application
# ---------------------------------------------------------------------------

class PyBlockRunnerApp:
    def __init__(self, root: tk.Tk, prefill_script: Optional[str] = None):
        self.root = root
        self.root.title("PyBlockRunner")
        self.root.resizable(True, True)
        self.root.minsize(700, 600)

        # Message queue for thread → GUI communication
        self._q: queue.Queue = queue.Queue()

        # State
        self._blocks: list = []
        self._block_vars: list[tk.BooleanVar] = []
        self._results: list[BlockResult] = []
        self._running = False
        self._out_path: Optional[Path] = None
        self._preview_photo: Optional[ImageTk.PhotoImage] = None

        self._build_ui()

        if prefill_script:
            self._script_var.set(prefill_script)
            self._on_file_selected()

        # Start the GUI event-queue polling loop
        self.root.after(POLL_MS, self._poll_queue)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)  # console expands

        # ── Top frame: file + options ──────────────────────────────────
        top = ttk.LabelFrame(root, text="Configuration", padding=8)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        top.columnconfigure(1, weight=1)

        # File row
        ttk.Label(top, text="Script:").grid(row=0, column=0, sticky="w")
        self._script_var = tk.StringVar()
        self._script_entry = ttk.Entry(top, textvariable=self._script_var, width=55)
        self._script_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self._script_entry.bind("<Return>", lambda _: self._on_file_selected())
        self._script_entry.bind("<FocusOut>", lambda _: self._on_file_selected())
        ttk.Button(top, text="Browse…", command=self._browse).grid(row=0, column=2)

        # Name / ID row (both fields are optional)
        ttk.Label(top, text="Name (optional):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._name_var = tk.StringVar()
        ttk.Entry(top, textvariable=self._name_var, width=24).grid(
            row=1, column=1, sticky="w", padx=4, pady=(6, 0))

        id_frame = ttk.Frame(top)
        id_frame.grid(row=1, column=1, sticky="e", pady=(6, 0))
        ttk.Label(id_frame, text="ID / Roll No (optional):").pack(side="left")
        self._id_var = tk.StringVar()
        ttk.Entry(id_frame, textvariable=self._id_var, width=16).pack(side="left", padx=4)

        # Options row
        opt_frame = ttk.Frame(top)
        opt_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        ttk.Label(opt_frame, text="Theme:").pack(side="left")
        self._theme_var = tk.StringVar(value="light")
        ttk.Combobox(opt_frame, textvariable=self._theme_var,
                     values=["light", "dark"], width=7, state="readonly").pack(
            side="left", padx=(2, 12))

        ttk.Label(opt_frame, text="State:").pack(side="left")
        self._state_var = tk.StringVar(value="Shared")
        ttk.Combobox(opt_frame, textvariable=self._state_var,
                     values=["Shared", "Isolated"], width=9, state="readonly").pack(
            side="left", padx=(2, 12))

        self._code_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="Include Code", variable=self._code_var).pack(
            side="left", padx=(0, 12))

        self._cover_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="Cover Page", variable=self._cover_var).pack(
            side="left", padx=(0, 12))

        ttk.Label(opt_frame, text="Timeout (s):").pack(side="left")
        self._timeout_var = tk.StringVar(value="30")
        ttk.Spinbox(opt_frame, textvariable=self._timeout_var, from_=0, to=300,
                    width=5).pack(side="left", padx=(2, 12))

        ttk.Label(opt_frame, text="Output PDF:").pack(side="left")
        self._outpath_var = tk.StringVar()
        ttk.Entry(opt_frame, textvariable=self._outpath_var, width=22).pack(
            side="left", padx=2)
        ttk.Button(opt_frame, text="…", width=2,
                   command=self._browse_output).pack(side="left")

        # Font row
        font_frame = ttk.Frame(top)
        font_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Label(font_frame, text="Document Font:").pack(side="left")
        self._font_var = tk.StringVar(value="")
        self._font_display_var = tk.StringVar(value="Default (Iosevka NF / System)")
        ttk.Label(font_frame, textvariable=self._font_display_var, foreground="#2563eb", font=("TkDefaultFont", 11, "bold")).pack(
            side="left", padx=(4, 6))
        ttk.Button(font_frame, text="Choose Font…", command=self._open_font_chooser).pack(side="left")
        ttk.Label(font_frame, text="(terminal output locked to Iosevka NF)", foreground="grey").pack(
            side="left", padx=(8, 0))

        # ── Block list ─────────────────────────────────────────────────
        block_frame = ttk.LabelFrame(root, text="Detected Blocks", padding=6)
        block_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        block_frame.columnconfigure(0, weight=1)

        self._block_list_frame = ttk.Frame(block_frame)
        self._block_list_frame.grid(row=0, column=0, sticky="ew")

        self._blocks_placeholder = ttk.Label(
            self._block_list_frame,
            text="  No file selected — open a Python script to detect blocks.",
            foreground="grey",
        )
        self._blocks_placeholder.grid(row=0, column=0, sticky="w")

        # ── Progress + console ─────────────────────────────────────────
        prog_frame = ttk.LabelFrame(root, text="Execution Progress", padding=6)
        prog_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        prog_frame.columnconfigure(0, weight=1)

        self._progress_label = ttk.Label(prog_frame, text="Ready.")
        self._progress_label.grid(row=0, column=0, sticky="w")

        self._progress_bar = ttk.Progressbar(prog_frame, mode="determinate", length=400)
        self._progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        # Run / Stop / Open PDF buttons
        btn_frame = ttk.Frame(prog_frame)
        btn_frame.grid(row=0, column=1, sticky="e")
        self._run_btn = ttk.Button(btn_frame, text="▶  Run", command=self._start_run,
                                   style="Accent.TButton")
        self._run_btn.pack(side="left", padx=2)
        self._open_btn = ttk.Button(btn_frame, text="Open PDF", command=self._open_result,
                                    state="disabled")
        self._open_btn.pack(side="left", padx=2)

        # Console
        console_frame = ttk.LabelFrame(root, text="Console Output", padding=4)
        console_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=4)
        console_frame.columnconfigure(0, weight=1)
        console_frame.rowconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        self._console = tk.Text(console_frame, height=10, wrap="word",
                                bg="#1a1a1a", fg="#d4d4d4",
                                font=("Courier New", 11), state="disabled")
        self._console.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(console_frame, command=self._console.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._console["yscrollcommand"] = sb.set

        # Configure console tags
        self._console.tag_config("ok", foreground="#6fcf97")
        self._console.tag_config("err", foreground="#eb5757")
        self._console.tag_config("info", foreground="#aaaaaa")
        self._console.tag_config("skip", foreground="#888888")

        # ── Preview ────────────────────────────────────────────────────
        preview_frame = ttk.LabelFrame(root, text="Latest Block Preview", padding=4)
        preview_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 10))

        self._preview_label = ttk.Label(preview_frame, text="(Preview will appear here)")
        self._preview_label.pack()

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
            self._font_display_var.set(font_name if font_name else "Default (Iosevka NF / System)")

        FontChooserDialog(self.root, self._font_var.get(), on_select)

    def _browse_font(self) -> None:
        self._open_font_chooser()

    def _on_file_selected(self) -> None:
        script = self._script_var.get().strip()
        if not script or not Path(script).exists():
            return
        # Auto-suggest output path
        p = Path(script)
        if not self._outpath_var.get():
            self._outpath_var.set(str(p.parent / f"{p.stem}_output.pdf"))
        # Parse blocks
        try:
            self._blocks = parse_file(script)
            self._refresh_block_list()
        except Exception as exc:
            self._log(f"[Error parsing file] {exc}\n", tag="err")

    def _refresh_block_list(self) -> None:
        """Rebuild the detected-blocks checklist."""
        for widget in self._block_list_frame.winfo_children():
            widget.destroy()
        self._block_vars.clear()

        if not self._blocks:
            ttk.Label(self._block_list_frame,
                      text="  No blocks detected.",
                      foreground="grey").grid(row=0, column=0, sticky="w")
            return

        for b in self._blocks:
            var = tk.BooleanVar(value=not b.skip)
            self._block_vars.append(var)
            label = b.title
            if b.skip:
                label += "  [skipped]"
            cb = ttk.Checkbutton(self._block_list_frame, text=label, variable=var)
            cb.grid(row=b.index - 1, column=0, sticky="w")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _start_run(self) -> None:
        script = self._script_var.get().strip()
        if not script:
            messagebox.showerror("No Script", "Please select a Python script first.")
            return
        if not Path(script).exists():
            messagebox.showerror("File Not Found", f"File not found:\n{script}")
            return
        if self._running:
            return

        self._running = True
        self._results = []
        self._out_path = None
        self._run_btn.config(state="disabled")
        self._open_btn.config(state="disabled")
        self._console.config(state="normal")
        self._console.delete("1.0", "end")
        self._console.config(state="disabled")

        # Gather settings
        isolated    = self._state_var.get() == "Isolated"
        theme       = self._theme_var.get()
        show_code   = self._code_var.get()
        no_cover    = not self._cover_var.get()
        timeout     = max(0, int(self._timeout_var.get() or "30"))
        out_path_str = self._outpath_var.get().strip()
        out_path    = Path(out_path_str) if out_path_str else None
        author_name = self._name_var.get().strip() or None
        author_id   = self._id_var.get().strip() or None

        # Resolve font
        from .renderer import resolve_font_path as _rfp
        raw_font = self._font_var.get().strip()
        font_path = _rfp(raw_font) if raw_font else None
        if raw_font and not font_path:
            self._log(f"[Warning] Font '{raw_font}' not found — using default.\n", "skip")

        # Rebuild block list with checkbox overrides
        try:
            blocks = parse_file(script)
        except Exception as exc:
            messagebox.showerror("Parse Error", str(exc))
            self._running = False
            self._run_btn.config(state="normal")
            return

        # Apply checkbox state (user may override skip annotations)
        for i, (block, var) in enumerate(zip(blocks, self._block_vars)):
            block.skip = not var.get()
        active_count = sum(1 for b in blocks if not b.skip)
        self._progress_bar["maximum"] = max(1, active_count)
        self._progress_bar["value"] = 0
        self._done_count = 0

        self._log(f"Running {active_count} block(s) from {Path(script).name}\n", "info")

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

            # Build PDF
            if out_path is None:
                p = Path(script)
                pdf_path = p.parent / f"{p.stem}_output.pdf"
            else:
                pdf_path = out_path

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

            self._q.put((0, "all_done", results))

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Queue polling & event dispatch
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
        if event == "start":
            self._progress_label.config(
                text=f"Running: {data}…"
            )
            self._log(f"\n▶ {data}\n", "info")

        elif event == "stdout":
            self._log(data)

        elif event == "stderr":
            self._log(data, "err")

        elif event == "skip":
            self._log(f"⊘ {data} (skipped)\n", "skip")

        elif event == "request_input":
            prompt = data["prompt"]
            b_idx = data["block_index"]
            b_title = data["block_title"]
            evt = data["event"]
            res = data["result"]

            display_prompt = prompt.strip() or "Input required"
            self._progress_label.config(
                text=f"⌨ Waiting for input: {display_prompt}"
            )
            self._log(f"⌨ Waiting for input: {display_prompt}…\n", "info")

            def _on_done(val: str):
                res[0] = val
                evt.set()
                self._log(f"→ Entered: {val}\n", "ok")
                self._progress_label.config(text=f"Running: {b_title}…")

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
            self._progress_bar["value"] = self._done_count
            ok = data["success"]
            ms = data["duration_ms"]
            time_str = f"{ms/1000:.2f}s" if ms >= 1000 else f"{ms:.0f}ms"
            tag = "ok" if ok else "err"
            sym = "✓" if ok else "✗"
            figs = data.get("figures", 0)
            fig_note = f"  [{figs} plot(s)]" if figs else ""
            self._log(f"{sym} {time_str}{fig_note}\n", tag)

        elif event == "preview":
            self._update_preview(data)

        elif event == "pdf_done":
            self._out_path = data
            self._log(f"\n✓ PDF saved → {data}\n", "ok")
            self._open_btn.config(state="normal")

        elif event == "pdf_error":
            self._log(f"\n✗ PDF build failed: {data}\n", "err")

        elif event == "all_done":
            self._results = data
            self._running = False
            self._run_btn.config(state="normal")
            total_ms = sum(r.duration_ms for r in data if not r.skipped)
            ts = f"{total_ms/1000:.2f}s"
            self._progress_label.config(
                text=f"Done! All blocks finished in {ts}."
            )
            # Show preview of last non-skipped result
            last = next((r for r in reversed(data) if not r.skipped), None)
            if last:
                self._render_and_preview(last)

    # ------------------------------------------------------------------
    # Console & preview helpers
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
        """Render the terminal output and push to preview in a background thread."""
        script = self._script_var.get().strip()
        raw_font = self._font_var.get().strip()
        from .renderer import resolve_font_path as _rfp
        font_path = _rfp(raw_font) if raw_font else None

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
            img.thumbnail((PREVIEW_MAX_W * 2, PREVIEW_MAX_H * 2), Image.LANCZOS)
            self._q.put((0, "preview", img))

        threading.Thread(target=_render, daemon=True).start()

    def _update_preview(self, img: Image.Image) -> None:
        photo = ImageTk.PhotoImage(img)
        self._preview_photo = photo   # keep reference
        self._preview_label.config(image=photo, text="")

    def _open_result(self) -> None:
        if self._out_path and self._out_path.exists():
            _open_pdf(self._out_path)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def launch_gui(args: Optional[argparse.Namespace] = None) -> None:
    """Create the Tk root window and start the main loop."""
    root = tk.Tk()

    # Try to give it a decent look on macOS
    try:
        root.tk.call("::tk::mac::standardAboutPanel")
    except Exception:
        pass

    prefill = None
    if args and getattr(args, "script", None):
        prefill = args.script

    app = PyBlockRunnerApp(root, prefill_script=prefill)  # noqa: F841

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
