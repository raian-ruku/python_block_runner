"""
parser.py - Block Parser for PyBlockRunner

Parses a Python source file into ordered Block objects, each corresponding
to a commented section. Uses a line-by-line state machine (not AST) so it
works even on syntactically broken scripts.

Supported separator patterns (at the start of a logical section):
  # %% Title
  # In[1]: Title
  # --- Title ---   /   # === Title ===
  ### Title
  # Problem 1: Title
  # Task 1: Title
  # Question 1: Title
  # Step 1: Title
  # Part A: Title
  # Section 1: Title

Any block whose comment contains "pyblock: skip" will be marked skip=True
and excluded from execution & PDF output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Block:
    index: int                  # 1-based serial
    title: str                  # Cleaned display title
    code: str                   # Executable Python source
    start_line: int             # 1-based line number of first code line
    end_line: int               # 1-based line number of last code line
    raw_comment: str = ""       # Original comment text (for debugging)
    skip: bool = False          # True if "pyblock: skip" annotation found
    full_source: str = ""       # Full script source for external reference resolution
    prelude: str = ""           # Script prelude (imports/setup before first block)


# ---------------------------------------------------------------------------
# Separator patterns  (checked in order; first match wins)
# ---------------------------------------------------------------------------

_SEPARATOR_PATTERNS: list[tuple[str, str, bool]] = [
    # Jupyter-style cell:  # %%  or  # %% Title
    (r"^#\s*%%+\s*(.*)", "jupyter", True),
    # Jupyter In[N]:  # In[3]: Title
    (r"^#\s*In\s*\[\s*\d*\s*\]\s*:?\s*(.*)", "jupyter_in", True),
    # Dashed/equals headers:  # --- Title ---  or  # === Title ===
    (r"^#\s*[-=]{3,}\s*(.*?)\s*[-=]*\s*$", "dashes", True),
    # Markdown-style hashes (in comments):  ### Title
    (r"^#{2,}\s+(.*)", "hashes", True),
    # Labeled sections (any label + number/letter + optional subtitle):
    # Problem 1:  Task A:  Question 2:  Step 3:  Part B:  Section 1:  Part 1
    (r"^#\s*((?:Problem|Task|Question|Step|Part|Section|Exercise|Lab|Assignment|Example|Case|Block)\s*[\w\d.]*)(?:[:\-–—\s]\s*(.*))?$",
     "labeled", True),
    # Generic bare separators: a top-level comment line that is ALL CAPS (NO ignore case)
    (r"^#\s*([A-Z0-9\s:,.()\-]{4,})\s*$", "caps_title", False),
]

_COMPILED: list[tuple[re.Pattern, str, bool]] = [
    (re.compile(pat, re.IGNORECASE if ic else 0), kind, ic)
    for pat, kind, ic in _SEPARATOR_PATTERNS
]

# Lines that are pure noise / not real separators:
_NOISE_PATTERN = re.compile(
    r"^#\s*(?:[-=*]{3,}|!|coding[:=]|type:\s*ignore|noqa|pylint|flake8|fmt:)\s*$",
    re.IGNORECASE
)

# pyblock skip annotation
_SKIP_PATTERN = re.compile(r"pyblock\s*:\s*skip", re.IGNORECASE)

_NON_TITLE_PREFIXES = (
    "todo", "fixme", "note", "bug", "warning", "tip", "caution", "hack", "xxx",
    "url", "http", "https", "see", "author", "copyright", "license",
    "import ", "from ", "def ", "class ", "return ", "if ", "else", "elif ",
    "try:", "except", "finally", "with ", "for ", "while "
)

_MINOR_WORDS = {
    "a", "an", "the", "and", "but", "or", "for", "nor", "on", "at", "to",
    "from", "by", "with", "without", "in", "of", "vs", "via"
}


def _is_title_comment(line: str) -> bool:
    """Return True if *line* looks like a section or problem title comment."""
    if line.startswith((" ", "\t")):
        return False
    stripped = line.strip()
    if not stripped.startswith("#"):
        return False
    if _NOISE_PATTERN.match(stripped):
        return False
    text = stripped.lstrip("#").strip()
    if len(text) < 2 or len(text) > 300:
        return False
    # Exclude complete sentences ending in terminal punctuation
    if text.endswith((".", "!", "?", ";", ",")):
        return False
    lower = text.lower()
    if any(lower.startswith(p) for p in _NON_TITLE_PREFIXES):
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters or not letters[0].isupper():
        return False
    words = [w for w in re.findall(r"[A-Za-z0-9_\-]+", text) if any(c.isalpha() for c in w)]
    if not words:
        return False
    if len(words) == 1:
        return words[0][0].isupper()
    cap_count = sum(1 for w in words if w[0].isupper())
    non_minor = [w for w in words[1:] if w.lower() not in _MINOR_WORDS]
    if not non_minor:
        return words[0][0].isupper()
    non_minor_caps = sum(1 for w in non_minor if w[0].isupper())
    return (non_minor_caps == len(non_minor)) or (cap_count / len(words) >= 0.7)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_file(path: str | Path, separator: str = "auto") -> list[Block]:
    """
    Parse *path* into an ordered list of :class:`Block` objects.

    Parameters
    ----------
    path:
        Path to the Python source file.
    separator:
        ``"auto"`` (default) — use all built-in patterns.
        Any other string is treated as a raw regex; only lines matching it
        trigger a new block.
    """
    source = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()

    if separator == "auto":
        matcher = _match_any
    else:
        _custom = re.compile(separator)
        def matcher(line: str) -> Optional[str]:  # noqa: E306
            if line.startswith((" ", "\t")):
                return None
            m = _custom.search(line)
            return m.group(1) if m and m.lastindex else (m.group(0) if m else None)

    return _split_into_blocks(lines, matcher, full_source=source)


def parse_source(source: str, separator: str = "auto") -> list[Block]:
    """Same as :func:`parse_file` but accepts a source string directly."""
    lines = source.splitlines()
    if separator == "auto":
        matcher = _match_any
    else:
        _custom = re.compile(separator)
        def matcher(line: str) -> Optional[str]:
            if line.startswith((" ", "\t")):
                return None
            m = _custom.search(line)
            return m.group(1) if m and m.lastindex else (m.group(0) if m else None)
    return _split_into_blocks(lines, matcher, full_source=source)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _match_any(line: str) -> Optional[str]:
    """Return extracted title if *line* is a separator comment, else None."""
    # Indented comments belong inside code blocks (loops, functions, classes, etc.)
    # and MUST NOT trigger top-level section breaks!
    if line.startswith((" ", "\t")):
        return None
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    if _NOISE_PATTERN.match(stripped):
        return None
    for pattern, kind, ic in _COMPILED:
        m = pattern.match(stripped)
        if m:
            if kind == "labeled":
                label = m.group(1).strip()
                desc = (m.group(2) or "").strip().lstrip(":-–— ").strip()
                return f"{label}: {desc}" if desc else label
            title = m.group(1).strip() if m.lastindex else stripped.lstrip("#").strip()
            if kind == "caps_title":
                letters = [c for c in title if c.isalpha()]
                if not letters or not all(c.isupper() for c in letters):
                    continue
            return title
    if _is_title_comment(stripped):
        return stripped.lstrip("#").strip()
    return None


def _is_in_multiline_string(lines: list[str], target_idx: int) -> bool:
    """
    Rough check: is line *target_idx* (0-based) inside a triple-quoted string?
    Counts triple-quote openers/closers up to that line.
    """
    in_triple = False
    triple_char: str = ""
    for i, line in enumerate(lines):
        if i == target_idx:
            break
        # Count occurrences of ''' and """ alternately (simplified)
        j = 0
        while j < len(line):
            for q in ('"""', "'''"):
                if line[j:j+3] == q:
                    if not in_triple:
                        in_triple = True
                        triple_char = q
                        j += 3
                        break
                    elif triple_char == q:
                        in_triple = False
                        triple_char = ""
                        j += 3
                        break
            else:
                j += 1
    return in_triple


def _clean_title(raw: str) -> str:
    """Strip trailing punctuation noise and trim whitespace."""
    t = raw.strip().rstrip("-=").strip()
    # Remove repeated hashes at the start (from ### style)
    t = t.lstrip("#").strip()
    return t


def _split_into_blocks(lines: list[str], matcher, full_source: str = "") -> list[Block]:
    """
    Core splitting logic.

    Walks through *lines*, building a list of (title, raw_comment, start_idx,
    code_lines).  Then converts those to :class:`Block` objects.
    """
    # Lines that occur before the first separator
    pending: list[dict] = []
    current_block: Optional[dict] = None
    prelude_lines: list[str] = []
    prelude_str: str = ""
    seen_first_separator = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip lines inside multiline strings (quick approximation)
        if _is_in_multiline_string(lines, i):
            if current_block is not None:
                current_block["code_lines"].append(line)
            else:
                prelude_lines.append(line)
            i += 1
            continue

        # Empty line
        if stripped == "":
            if current_block is not None:
                current_block["code_lines"].append(line)
            else:
                prelude_lines.append(line)
            i += 1
            continue

        # Comment line
        if stripped.startswith("#"):
            title = matcher(line)
            if title is not None:
                # Separator comment found!
                if current_block is not None:
                    pending.append(current_block)

                skip = bool(_SKIP_PATTERN.search(line)) or (
                    i + 1 < len(lines) and bool(_SKIP_PATTERN.search(lines[i + 1]))
                )
                code_start_idx = i + 1
                # Save prelude code (imports/setup before first block) separately
                if not seen_first_separator and prelude_lines:
                    prelude_str = "\n".join(prelude_lines).strip()
                    prelude_lines.clear()

                seen_first_separator = True
                clean_t = _clean_title(title)

                current_block = {
                    "title": clean_t,
                    "raw_comment": line.strip(),
                    "comment_line": i + 1,
                    "code_start": code_start_idx,
                    "code_lines": [],
                    "skip": skip,
                }
            else:
                if current_block is not None:
                    current_block["code_lines"].append(line)
                else:
                    prelude_lines.append(line)
            i += 1
            continue

        # Code line
        if current_block is None:
            # Code before any separator comment → save into prelude
            prelude_lines.append(line)
        else:
            current_block["code_lines"].append(line)
        i += 1

    # Close the last open block
    if current_block is not None:
        pending.append(current_block)

    # If the file had NO separator comments at all, wrap the entire file as one single block
    if not pending and prelude_lines:
        code_str = "\n".join(prelude_lines).strip()
        if code_str:
            pending.append({
                "title": "Script Output",
                "raw_comment": "",
                "comment_line": 1,
                "code_start": 0,
                "code_lines": prelude_lines,
                "skip": False,
            })

    # Convert to Block dataclasses, drop empty blocks unless they have a distinct title
    blocks: list[Block] = []
    idx = 1
    for p in pending:
        code = "\n".join(p["code_lines"]).strip()
        if not code and (not p["title"] or p["title"] == "Untitled Block"):
            continue
        if p["code_lines"]:
            start = p["code_start"] + 1
            end = max(start, start + len(p["code_lines"]) - 1)
        else:
            start = p.get("comment_line", p["code_start"] + 1)
            end = start
        block_title = p["title"] if p["title"] and p["title"] != "Untitled Block" else f"Block {idx}"
        blocks.append(Block(
            index=idx,
            title=block_title,
            code=code,
            start_line=start,
            end_line=end,
            raw_comment=p["raw_comment"],
            skip=p["skip"],
            full_source=full_source,
            prelude=prelude_str,
        ))
        idx += 1

    return blocks
