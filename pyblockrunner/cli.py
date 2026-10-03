"""
cli.py - Command-Line Interface for PyBlockRunner

Entry point for the `pyblockrunner` command.

Usage:
    pyblockrunner script.py [options]

Flags:
    --version / -v          Print version and exit
    --name <str>            Author/student name for PDF cover page
    --id <str>              Student/employee ID for PDF cover page
    --gui                   Force open the Desktop GUI window
    --no-gui                Force headless CLI execution (no GUI)
    --output / -o <path>    Full output PDF path
    --output-dir <dir>      Output directory (PDF named automatically)
    --theme [dark|light]    Terminal card colour theme (default: dark)
    --isolated              Run each block in a clean namespace
    --no-code               Omit code snippets from screenshots
    --timeout <int>         Max seconds per block (default: 30, 0=unlimited)
    --open                  Open PDF in system viewer when done
    --separator <regex>     Custom block separator regex (default: auto)
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

from . import __version__


def _open_pdf(path: Path) -> None:
    """Open the PDF in the system default viewer."""
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif platform.system() == "Windows":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        print(f"[pyblockrunner] Could not open PDF automatically: {exc}")


def _run_headless(args: argparse.Namespace) -> int:
    """Execute blocks and build PDF without a GUI."""
    from .parser import parse_file
    from .executor import Executor
    from .pdf_builder import build_pdf

    script = Path(args.script)
    if not script.exists():
        print(f"[pyblockrunner] Error: file not found: {script}", file=sys.stderr)
        return 1

    # Determine output path
    if args.output:
        out_path = Path(args.output)
    elif args.output_dir:
        stem = script.stem
        out_path = Path(args.output_dir) / f"{stem}_output.pdf"
    else:
        out_path = script.parent / f"{script.stem}_output.pdf"

    print(f"[pyblockrunner] Parsing: {script.name}")
    blocks = parse_file(script, separator=args.separator or "auto")
    active = [b for b in blocks if not b.skip]
    print(f"[pyblockrunner] Found {len(blocks)} block(s) ({len(active)} active, "
          f"{len(blocks) - len(active)} skipped)")

    def progress(idx: int, event: str, data=None) -> None:
        if event == "start":
            print(f"  ▶ {data}", flush=True)
        elif event == "done" and isinstance(data, dict):
            status = "✓" if data["success"] else "✗"
            ms = data["duration_ms"]
            time_str = f"{ms/1000:.2f}s" if ms >= 1000 else f"{ms:.0f}ms"
            figs = data["figures"]
            fig_note = f"  [{figs} plot(s)]" if figs else ""
            print(f"  {status} {time_str}{fig_note}", flush=True)
        elif event == "skip":
            print(f"  ⊘ {data} (skipped)", flush=True)

    source_text = script.read_text(encoding="utf-8", errors="replace")
    executor = Executor(
        isolated=args.isolated,
        timeout=float(args.timeout),
        progress_callback=progress,
        full_source=source_text,
    )
    results = executor.run(blocks)

    print(f"\n[pyblockrunner] Building PDF → {out_path}")
    from .renderer import resolve_font_path
    font_path = resolve_font_path(args.font) if getattr(args, "font", None) else None
    if getattr(args, "font", None) and not font_path:
        print(f"[pyblockrunner] Warning: font '{args.font}' not found, using default.")
    build_pdf(
        results=results,
        output_path=out_path,
        script_path=script,
        script_name=script.name,
        theme=args.theme,
        show_code=getattr(args, "code", False) and not getattr(args, "no_code", False),
        author_name=args.name or None,
        author_id=args.id or None,
        no_cover=getattr(args, "no_cover", False) or not getattr(args, "cover", False),
        font_path=font_path,
    )
    print(f"[pyblockrunner] Done ✓  PDF saved to: {out_path}")

    if args.open:
        _open_pdf(out_path)

    return 0


def _run_gui(args: argparse.Namespace) -> int:
    """Launch the native desktop GUI window."""
    try:
        from .gui import launch_gui
        launch_gui(args)
        return 0
    except ImportError as exc:
        print(f"[pyblockrunner] GUI unavailable: {exc}", file=sys.stderr)
        print("[pyblockrunner] Falling back to headless mode.", file=sys.stderr)
        return _run_headless(args)


def _has_display() -> bool:
    """Return True if a display is available for a GUI window."""
    if platform.system() == "Darwin":
        return True
    if platform.system() == "Windows":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyblockrunner",
        description=(
            "Execute commented Python blocks, screenshot each output, "
            "and compile them into a PDF."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("script", nargs="?", help="Path to the target Python script.")
    p.add_argument("--version", "-v", action="version",
                   version=f"pyblockrunner {__version__}")
    p.add_argument("--name", metavar="NAME",
                   help="Author/student name for the PDF cover page.")
    p.add_argument("--id", metavar="ID",
                   help="Student/employee ID for the PDF cover page.")
    p.add_argument("--gui", action="store_true",
                   help="Force open the Desktop GUI window.")
    p.add_argument("--no-gui", action="store_true",
                   help="Force headless (terminal-only) execution.")
    p.add_argument("--output", "-o", metavar="PATH",
                   help="Full path for the output PDF.")
    p.add_argument("--output-dir", metavar="DIR",
                   help="Output directory (PDF named <script>_output.pdf).")
    p.add_argument("--theme", choices=["light", "dark"], default="light",
                   help="Terminal card colour theme (default: light).")
    p.add_argument("--isolated", action="store_true",
                   help="Run each block in a clean namespace (no variable sharing).")
    p.add_argument("--code", action="store_true",
                   help="Include code snippets in screenshot cards.")
    p.add_argument("--no-code", action="store_true",
                   help="Omit code snippets from screenshot cards.")
    p.add_argument("--timeout", type=int, default=30, metavar="SECONDS",
                   help="Max execution seconds per block (0 = no limit, default: 30).")
    p.add_argument("--open", action="store_true",
                   help="Open the generated PDF in the system viewer when done.")
    p.add_argument("--separator", metavar="REGEX",
                   help="Custom block separator regex (default: auto-detect).")
    p.add_argument("--cover", action="store_true",
                   help="Include a cover/title page in the output PDF.")
    p.add_argument("--no-cover", action="store_true",
                   help="Skip the cover/title page in the output PDF.")
    p.add_argument("--font", metavar="NAME_OR_PATH",
                   help=(
                       "Font name or TTF/OTF path for terminal output & code boxes. "
                       "E.g. --font 'Iosevka NF' or "
                       "--font ~/Library/Fonts/IosevkaNerdFont-Regular.ttf"
                   ))
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # No script provided → open GUI if possible, else print help
    if args.script is None:
        if not args.no_gui and _has_display():
            _run_gui(args)
        else:
            parser.print_help()
        sys.exit(0)

    # Explicit --gui flag
    if args.gui:
        sys.exit(_run_gui(args))

    # Explicit --no-gui flag or no display
    if args.no_gui or not _has_display():
        sys.exit(_run_headless(args))

    # Default: open GUI with the pre-filled script path
    sys.exit(_run_gui(args))


if __name__ == "__main__":
    main()
