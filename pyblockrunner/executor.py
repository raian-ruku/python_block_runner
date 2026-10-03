"""
executor.py - Block Execution Engine for PyBlockRunner

Executes Python code blocks sequentially, capturing:
  - stdout / stderr  (real-time, line-buffered)
  - matplotlib figures (via plt.show() hook and figure inspection)
  - exceptions / tracebacks (non-fatal; the next block still runs)

Supports:
  - Shared namespace (default): variables/imports carry across blocks.
  - Isolated namespace: each block gets a fresh dict.
  - Per-block timeout using threading.Timer + daemon thread.
  - input() auto-stubbing to prevent hangs.
  - Optional progress callback: called with (block_index, event_type, data).
"""

from __future__ import annotations

import builtins
import io
import re
import sys
import time
import textwrap
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from PIL import Image


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BlockResult:
    index: int
    title: str
    code: str
    stdout: str              # Captured standard output
    stderr: str              # Captured standard error / traceback
    figures: list[Image.Image] = field(default_factory=list)  # matplotlib figs
    duration_ms: float = 0.0
    success: bool = True
    error_msg: str = ""      # Short error summary (empty on success)
    skipped: bool = False


# ---------------------------------------------------------------------------
# Matplotlib interception helper
# ---------------------------------------------------------------------------

def _install_matplotlib_hook(figure_sink: list[Image.Image]) -> Optional[Any]:
    """
    Monkey-patch plt.show() so figures are captured instead of displayed.
    Returns the original plt.show reference (for restoring later), or None
    if matplotlib is not available in the user's environment.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")          # Non-interactive backend
        import matplotlib.pyplot as plt

        original_show = plt.show

        def _capture_show(*args, **kwargs):
            import matplotlib.pyplot as _plt
            for num in _plt.get_fignums():
                fig = _plt.figure(num)
                buf = io.BytesIO()
                fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
                buf.seek(0)
                figure_sink.append(Image.open(buf).copy())
            _plt.close("all")

        plt.show = _capture_show
        return original_show
    except ImportError:
        return None


def _remove_matplotlib_hook(original_show: Any) -> None:
    """Restore the original plt.show (no-op if matplotlib not available)."""
    if original_show is None:
        return
    try:
        import matplotlib.pyplot as plt
        plt.show = original_show
    except ImportError:
        pass


def _harvest_open_figures(figure_sink: list[Image.Image]) -> None:
    """
    Harvest any figures that were created but NOT followed by plt.show().
    This handles code that forgets to call show(), or uses savefig() style.
    """
    try:
        import matplotlib.pyplot as plt
        for num in plt.get_fignums():
            fig = plt.figure(num)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
            buf.seek(0)
            figure_sink.append(Image.open(buf).copy())
        plt.close("all")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------

def _make_input_stub(captured_stderr: io.StringIO) -> Callable:
    """Return a safe input() that echoes a warning and returns empty string."""
    def _stub(prompt=""):
        msg = f"[pyblockrunner] input() call intercepted (prompt={prompt!r}). Returning ''.\n"
        captured_stderr.write(msg)
        if prompt:
            return ""
        return ""
    return _stub


class _TimeoutError(Exception):
    """Raised inside the execution thread when the block times out."""


class _StreamCapture(io.StringIO):
    """StringIO that optionally calls a live callback on each write with an output cap."""

    MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB ceiling to prevent runaway memory/queue floods

    def __init__(self, callback: Optional[Callable[[str], None]] = None):
        super().__init__()
        self._callback = callback
        self._bytes_written = 0
        self._capped = False

    def write(self, s: str) -> int:
        if self._capped:
            return 0
        chunk_len = len(s.encode("utf-8", errors="replace"))
        if self._bytes_written + chunk_len > self.MAX_BYTES:
            self._capped = True
            trunc_msg = "\n[pyblockrunner: output capped at 5MB limit to prevent freeze]\n"
            super().write(trunc_msg)
            if self._callback:
                self._callback(trunc_msg)
            return len(s)
        self._bytes_written += chunk_len
        result = super().write(s)
        if self._callback and s:
            self._callback(s)
        return result

    def truncate(self, size: Optional[int] = None) -> int:
        res = super().truncate(size)
        if size == 0:
            self._bytes_written = 0
            self._capped = False
        return res


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class Executor:
    """
    Runs a list of :class:`~pyblockrunner.parser.Block` objects and returns
    a list of :class:`BlockResult` objects.

    Parameters
    ----------
    isolated:
        If True each block gets a fresh ``exec`` namespace.
        If False (default) the namespace is shared across blocks.
    timeout:
        Maximum seconds allowed per block.  0 = no limit.
    progress_callback:
        Called on each event with (block_index: int, event: str, data: Any).
        Events: ``"start"``, ``"stdout"``, ``"stderr"``, ``"done"``, ``"skip"``.
    """

    def __init__(
        self,
        isolated: bool = False,
        timeout: float = 30.0,
        progress_callback: Optional[Callable[[int, str, Any], None]] = None,
        full_source: str = "",
        input_handler: Optional[Callable[..., str]] = None,
    ):
        self.isolated = isolated
        self.timeout = timeout
        self.progress_callback = progress_callback
        self.full_source = full_source
        self.input_handler = input_handler
        self._namespace: dict = {}

    def _emit(self, index: int, event: str, data: Any = None) -> None:
        if self.progress_callback:
            try:
                self.progress_callback(index, event, data)
            except Exception:
                pass

    def run(self, blocks: list, full_source: Optional[str] = None) -> list[BlockResult]:
        """Execute all blocks and return results in serial order."""
        if full_source is not None:
            self.full_source = full_source
        elif not self.full_source and blocks:
            first_fs = getattr(blocks[0], "full_source", "")
            if first_fs:
                self.full_source = first_fs

        results: list[BlockResult] = []

        if not self.isolated:
            # Shared namespace: pre-populate with builtins
            self._namespace = {"__builtins__": __builtins__}

        for block in blocks:
            result = self._run_block(block)
            results.append(result)

        return results

    def _run_block(self, block) -> BlockResult:
        """Execute a single block and return its :class:`BlockResult`."""
        if block.skip:
            self._emit(block.index, "skip", block.title)
            return BlockResult(
                index=block.index,
                title=block.title,
                code=block.code,
                stdout="",
                stderr="",
                skipped=True,
            )

        self._emit(block.index, "start", block.title)

        # Fresh namespace for isolated mode
        ns = {} if self.isolated else self._namespace
        if not ns.get("__builtins__"):
            ns["__builtins__"] = __builtins__

        # Capture streams
        captured_stdout = _StreamCapture(
            callback=lambda s: self._emit(block.index, "stdout", s)
        )
        captured_stderr = _StreamCapture(
            callback=lambda s: self._emit(block.index, "stderr", s)
        )

        # Figure sink list (populated by matplotlib hook)
        figures: list[Image.Image] = []
        original_show = _install_matplotlib_hook(figures)

        waiting_for_input = threading.Event()
        total_input_wait = [0.0]

        def _custom_input(prompt=""):
            prompt_str = str(prompt) if prompt is not None else ""
            if prompt_str:
                captured_stdout.write(prompt_str)

            user_val = ""
            if self.input_handler:
                waiting_for_input.set()
                t0 = time.perf_counter()
                try:
                    try:
                        user_val = self.input_handler(prompt_str, block.index, block.title)
                    except TypeError:
                        user_val = self.input_handler(prompt_str)
                except Exception as exc:
                    captured_stderr.write(f"[pyblockrunner] input() handler error: {exc}\n")
                    user_val = ""
                finally:
                    waiting_for_input.clear()
                    total_input_wait[0] += (time.perf_counter() - t0)
            elif sys.stdin.isatty():
                sys.__stdout__.write(prompt_str)
                sys.__stdout__.flush()
                waiting_for_input.set()
                t0 = time.perf_counter()
                try:
                    line = sys.__stdin__.readline()
                    user_val = line.rstrip("\r\n") if line else ""
                finally:
                    waiting_for_input.clear()
                    total_input_wait[0] += (time.perf_counter() - t0)
            else:
                user_val = ""

            captured_stdout.write(f"{user_val}\n")
            return user_val

        ns["input"] = _custom_input

        # Result container shared with the execution thread
        exec_result: dict = {"success": True, "error_msg": ""}

        # Dedent the code so indented blocks (e.g. inside ifs) still execute
        code = textwrap.dedent(block.code)

        # Pre-resolve external dependencies declared outside the block
        fs = self.full_source or getattr(block, "full_source", "")
        if fs:
            try:
                from .resolver import resolve_dependencies
                block_start = getattr(block, "start_line", 1)
                preamble = resolve_dependencies(
                    full_source=fs,
                    block_code=block.code,
                    current_ns=ns,
                    block_start_line=block_start,
                )
                if preamble:
                    exec(compile(preamble, "<dependencies>", "exec"), ns)
            except Exception:
                pass

        def _execute():
            original_builtin_input = builtins.input
            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout = captured_stdout
            sys.stderr = captured_stderr
            try:
                builtins.input = _custom_input
                max_retries = 5
                retry_count = 0
                while True:
                    try:
                        exec(compile(code, f"<block {block.index}>", "exec"), ns)
                        break
                    except NameError as name_err:
                        retry_count += 1
                        if retry_count > max_retries or not fs:
                            raise
                        missing_var = getattr(name_err, "name", None)
                        if not missing_var:
                            m = re.search(r"name '(\w+)' is not defined", str(name_err))
                            missing_var = m.group(1) if m else None
                        if not missing_var or missing_var in ns:
                            raise
                        from .resolver import resolve_single_symbol
                        block_start = getattr(block, "start_line", 1)
                        dep_code = resolve_single_symbol(
                            full_source=fs,
                            symbol=missing_var,
                            current_ns=ns,
                            block_start_line=block_start,
                        )
                        if not dep_code:
                            raise
                        # Execute dependency into ns quietly
                        sys.stdout = old_stdout
                        sys.stderr = old_stderr
                        try:
                            exec(compile(dep_code, f"<resolved {missing_var}>", "exec"), ns)
                        finally:
                            sys.stdout = captured_stdout
                            sys.stderr = captured_stderr
                        # Reset output buffers from aborted attempt before retry
                        captured_stdout.truncate(0)
                        captured_stdout.seek(0)
                        captured_stderr.truncate(0)
                        captured_stderr.seek(0)
            except Exception as exc:
                exec_result["success"] = False
                exec_result["error_msg"] = f"{type(exc).__name__}: {exc}"
                traceback.print_exc(file=captured_stderr)
            finally:
                builtins.input = original_builtin_input
                sys.stdout = old_stdout
                sys.stderr = old_stderr

        start_ts = time.perf_counter()

        if self.timeout > 0:
            thread = threading.Thread(target=_execute, daemon=True)
            thread.start()
            poll_interval = 0.1
            elapsed_code_time = 0.0
            while thread.is_alive():
                thread.join(timeout=poll_interval)
                if not waiting_for_input.is_set():
                    elapsed_code_time += poll_interval
                    if self.timeout > 0 and elapsed_code_time >= self.timeout:
                        exec_result["success"] = False
                        exec_result["error_msg"] = (
                            f"TimeoutError: Block exceeded {self.timeout}s limit."
                        )
                        captured_stderr.write(exec_result["error_msg"] + "\n")
                        break
        else:
            _execute()

        duration_ms = max(0.0, (time.perf_counter() - start_ts - total_input_wait[0]) * 1000)

        # Harvest any figures that survived without plt.show()
        _harvest_open_figures(figures)
        _remove_matplotlib_hook(original_show)

        out = captured_stdout.getvalue()
        err = captured_stderr.getvalue()

        self._emit(block.index, "done", {
            "success": exec_result["success"],
            "duration_ms": duration_ms,
            "figures": len(figures),
        })

        return BlockResult(
            index=block.index,
            title=block.title,
            code=block.code,
            stdout=out,
            stderr=err,
            figures=figures,
            duration_ms=duration_ms,
            success=exec_result["success"],
            error_msg=exec_result["error_msg"],
        )
