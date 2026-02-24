#!/usr/bin/env python3
"""
Generate PreTeXt output blocks from executed Jupyter notebooks.

This script reads executed Jupyter notebooks (from the soln/ and examples/
directories) and updates the PreTeXt source files to include the rendered
outputs (text results, figures, tables) after each Python code block.

Usage:
    cd /path/to/ThinkStats
    python pretext/generate_outputs.py
"""

import base64
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PRETEXT_DIR = REPO_ROOT / "pretext"
SOURCE_DIR = PRETEXT_DIR / "source"
ASSETS_DIR = PRETEXT_DIR / "assets"
IMAGES_DIR = ASSETS_DIR / "images"

# Map each PTX source file to its corresponding Jupyter notebook
CHAPTER_MAPPING = {
    "ch01-exploratory-data-analysis.ptx": REPO_ROOT / "soln" / "chap01.ipynb",
    "ch02-distributions.ptx": REPO_ROOT / "soln" / "chap02.ipynb",
    "ch03-pmf.ptx": REPO_ROOT / "soln" / "chap03.ipynb",
    "ch04-cdf.ptx": REPO_ROOT / "soln" / "chap04.ipynb",
    "ch05-modeling-distributions.ptx": REPO_ROOT / "soln" / "chap05.ipynb",
    "ch06-pdf.ptx": REPO_ROOT / "soln" / "chap06.ipynb",
    "ch07-relationships.ptx": REPO_ROOT / "soln" / "chap07.ipynb",
    "ch08-estimation.ptx": REPO_ROOT / "soln" / "chap08.ipynb",
    "ch09-hypothesis-testing.ptx": REPO_ROOT / "soln" / "chap09.ipynb",
    "ch10-least-squares.ptx": REPO_ROOT / "soln" / "chap10.ipynb",
    "ch11-multiple-regression.ptx": REPO_ROOT / "soln" / "chap11.ipynb",
    "ch12-time-series-analysis.ptx": REPO_ROOT / "soln" / "chap12.ipynb",
    "ch13-survival-analysis.ptx": REPO_ROOT / "soln" / "chap13.ipynb",
    "ch14-analytic-methods.ptx": REPO_ROOT / "soln" / "chap14.ipynb",
    "appendix-fourier.ptx": REPO_ROOT / "examples" / "fourier.ipynb",
    "appendix-moneyline.ptx": REPO_ROOT / "examples" / "moneyline.ipynb",
    "appendix-ripoff-etf.ptx": REPO_ROOT / "examples" / "ripoff_etf.ipynb",
    "appendix-temperature.ptx": REPO_ROOT / "examples" / "temperature.ipynb",
    "appendix-variability.ptx": REPO_ROOT / "examples" / "variability.ipynb",
}

# Maximum length for text output (in characters) before truncation
MAX_TEXT_LENGTH = 2000


def normalize_code(code):
    """Normalize code string for comparison by stripping whitespace.

    Also un-escapes XML entities since PTX uses XML encoding but
    notebook source uses raw Python characters.
    """
    code = code.strip()
    # Unescape common XML entities so PTX code matches notebook code
    code = code.replace("&amp;", "&")
    code = code.replace("&lt;", "<")
    code = code.replace("&gt;", ">")
    code = code.replace("&apos;", "'")
    code = code.replace("&quot;", '"')
    return code


def xml_escape(text):
    """Escape special XML characters in text."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def html_table_to_ptx(html_str, indent="    "):
    """Convert an HTML simpletable to a PTX <table><tabular> element.

    Args:
        html_str: HTML string containing a <table class="simpletable"> element.
        indent: Leading whitespace for indentation.

    Returns:
        PTX XML string, or None if parsing fails.
    """

    class _TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self._current_row = []
            self._current_cell = None
            self._current_is_header = False

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self._current_row = []
            elif tag in ("td", "th"):
                self._current_cell = ""
                self._current_is_header = tag == "th"

        def handle_endtag(self, tag):
            if tag == "tr":
                if self._current_row is not None:
                    self.rows.append(self._current_row)
                self._current_row = []
            elif tag in ("td", "th"):
                if self._current_cell is not None:
                    self._current_row.append(
                        (self._current_is_header, self._current_cell.strip())
                    )
                self._current_cell = None

        def handle_data(self, data):
            if self._current_cell is not None:
                self._current_cell += data

    parser = _TableParser()
    parser.feed(html_str)

    rows = [r for r in parser.rows if r]
    if not rows:
        return None

    inner_indent = indent + "  "
    row_indent = indent + "    "
    cell_indent = indent + "      "

    lines = [f"{indent}<table>", f"{inner_indent}<tabular halign=\"center\">"]
    for row_idx, row in enumerate(rows):
        # The first row is treated as a header row when any cell after the
        # first is a <th> element (statsmodels simpletable marks column headers
        # with <th>; the first cell is either empty or a row label).
        # row[1:] is safe because empty rows are already filtered out above.
        is_header = row_idx == 0 and any(is_th for is_th, _ in row[1:])
        if is_header:
            lines.append(f'{row_indent}<row header="yes" bottom="minor">')
        else:
            lines.append(f"{row_indent}<row>")
        for _, cell_text in row:
            lines.append(f"{cell_indent}<cell>{xml_escape(cell_text)}</cell>")
        lines.append(f"{row_indent}</row>")

    lines.append(f"{inner_indent}</tabular>")
    lines.append(f"{indent}</table>")
    return "\n".join(lines)


def extract_outputs_from_notebook(notebook_path):
    """
    Read a Jupyter notebook and return a dict mapping normalized code to
    a list of rendered output blocks (as PTX XML strings).

    Returns dict: {normalized_code: [ptx_output_str, ...]}
    """
    if not notebook_path.exists():
        print(f"  WARNING: Notebook not found: {notebook_path}")
        return {}

    with open(notebook_path) as f:
        nb = json.load(f)

    cells = nb.get("cells", [])
    chapter_stem = notebook_path.stem  # e.g. "chap01"

    code_to_outputs = {}
    image_counter = [0]  # use list for mutability in nested function

    def process_outputs(cell_outputs, chapter_stem):
        """Convert notebook cell outputs to PTX XML strings."""
        ptx_blocks = []
        for output in cell_outputs:
            output_type = output.get("output_type", "")

            if output_type in ("execute_result", "display_data"):
                data = output.get("data", {})

                # Prefer image if available
                if "image/png" in data:
                    image_counter[0] += 1
                    img_filename = f"{chapter_stem}_{image_counter[0]:03d}.png"
                    img_path = IMAGES_DIR / img_filename
                    img_bytes = base64.b64decode(data["image/png"])
                    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                    with open(img_path, "wb") as f:
                        f.write(img_bytes)
                    ptx_blocks.append(
                        f'    <figure>\n'
                        f'      <image source="images/{img_filename}" width="80%"/>\n'
                        f'    </figure>'
                    )
                elif "text/html" in data:
                    # Convert HTML simpletables (e.g. from statsmodels) to PTX.
                    html = "".join(data["text/html"])
                    if 'class="simpletable"' in html:
                        ptx_table = html_table_to_ptx(html)
                        if ptx_table:
                            ptx_blocks.append(ptx_table)
                    elif "text/plain" in data:
                        text = "".join(data["text/plain"])
                        if text.strip() and not _is_boring_output(text):
                            text = _truncate(text)
                            text = xml_escape(text)
                            ptx_blocks.append(
                                f"    <pre>\n{text}\n    </pre>"
                            )
                elif "text/plain" in data:
                    # Use text/plain representation
                    text = "".join(data["text/plain"])
                    # Skip uninformative outputs
                    if text.strip() and not _is_boring_output(text):
                        text = _truncate(text)
                        text = xml_escape(text)
                        ptx_blocks.append(
                            f"    <pre>\n{text}\n    </pre>"
                        )

            elif output_type == "stream":
                text = "".join(output.get("text", []))
                if text.strip():
                    text = _truncate(text)
                    text = xml_escape(text)
                    ptx_blocks.append(
                        f"    <pre>\n{text}\n    </pre>"
                    )

            # Skip error outputs and other types

        return ptx_blocks

    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        outputs = cell.get("outputs", [])
        if not outputs:
            continue

        key = normalize_code(source)
        if not key:
            continue

        ptx_blocks = process_outputs(outputs, chapter_stem)
        if ptx_blocks:
            code_to_outputs[key] = ptx_blocks

    return code_to_outputs


def _is_boring_output(text):
    """Return True if the output is not worth showing (e.g. object repr)."""
    boring_patterns = [
        r"^<matplotlib\..*>$",
        r"^<Figure .*>$",
        r"^Text\(",
        r"^<IPython\.core\.display\.",
        r"^\(\)$",
        r"^None$",
    ]
    stripped = text.strip()
    for pattern in boring_patterns:
        if re.match(pattern, stripped):
            return True
    return False


def _truncate(text, max_length=MAX_TEXT_LENGTH):
    """Truncate text output if it's too long."""
    if len(text) > max_length:
        text = text[:max_length] + "\n... (output truncated)"
    return text


# Regex pattern string for a <program language="python"> block with its
# <input>…</input> content.  Groups (when used with re.DOTALL):
#   1: prog_indent   — leading whitespace of the <program> tag, e.g. "    "
#   2: open_tag      — "<program language="python">"
#   3: input_open    — whitespace + "<input>", e.g. "\n      <input>"
#   4: code          — raw code between <input> and </input>
#   5: input_close   — "</input>"
#   6: close_prog    — whitespace + "</program>", e.g. "\n    </program>"
#
# The code group uses a negative lookahead (?:(?!</input>).)* to prevent
# matching across </input> when two program blocks appear consecutively.
_PROG_PAT = (
    r"([ \t]*)(<program language=\"python\">)"
    r"(\s*<input>)((?:(?!</input>).)*?)(</input>)"
    r"(\s*</program>)"
)

def _build_console_block(prog_indent, input_open, code, input_close, close_tag, pre_blocks):
    """
    Build a <console> element with <input> and <output> from a <program>
    element and one or more <pre> output blocks.

    Arguments:
        prog_indent   -- leading whitespace of the <program> tag (e.g. "    ")
        input_open    -- whitespace + "<input>" (e.g. "\n      <input>")
        code          -- raw code content between <input> and </input>
        input_close   -- "</input>"
        close_tag     -- whitespace + "</program>" or whitespace + "</console>",
                         used to derive the closing indent/tag for </console>
        pre_blocks    -- string containing one or more <pre>…</pre> blocks
    """
    # Detect input indent from the whitespace before <input>
    input_indent_m = re.search(r"([ \t]+)<input>", input_open)
    input_indent = input_indent_m.group(1) if input_indent_m else prog_indent + "  "

    # Extract text from each <pre> block, deduplicate, and concatenate
    seen: set = set()
    text_parts = []
    for m in re.finditer(r"<pre>(.*?)</pre>", pre_blocks, re.DOTALL):
        text = m.group(1).rstrip()
        if text not in seen:
            seen.add(text)
            text_parts.append(text)
    combined_text = "".join(text_parts)

    output_elem = (
        f"\n{input_indent}<output>{combined_text}\n{input_indent}</output>"
    )
    close_console = re.sub(r"</program>|</console>", "</console>", close_tag)

    return (
        prog_indent
        + "<console>"
        + input_open
        + code
        + input_close
        + output_elem
        + close_console
    )


# Pattern matching a <console> block whose <output> contains only a boring
# Python class representation (e.g. "<class 'statsmodels.iolib.table.SimpleTable'>").
# These were generated by previous runs when text/html output was not handled.
_BORING_CONSOLE_PAT = re.compile(
    r"([ \t]*)<console>"
    r"(\s*<input>)((?:(?!</input>).)*?)(</input>)"
    r"\s*<output>\s*&lt;class\s+'[^']*'&gt;\s*</output>"
    r"(\s*</console>)",
    re.DOTALL,
)


def clean_boring_console_outputs(content):
    """Revert <console> blocks whose <output> contains only a boring Python
    class representation back to <program> blocks, so that update_ptx_file
    can replace them with proper table output.

    Returns the updated content string.
    """

    def _replacer(m):
        prog_indent = m.group(1)
        input_open = m.group(2)
        code = m.group(3)
        input_close = m.group(4)
        close_tag = m.group(5).replace("</console>", "</program>")
        return (
            f"{prog_indent}<program language=\"python\">"
            f"{input_open}{code}{input_close}{close_tag}"
        )

    return _BORING_CONSOLE_PAT.sub(_replacer, content)


def migrate_ptx_file(ptx_path):
    """
    Migrate an existing PTX file so that text output blocks (<pre>) are
    represented as <console><input>…</input><output>…</output></console>
    inside the <listing>, and figure output blocks (<figure>) stay after
    </listing>.

    PreTeXt's <listing> schema only allows Program or Console as content.
    <pre> elements are not valid children of <listing>, but <console> is
    and its <output> child carries the text output.

    Handles three cases:
    1. A separate <listing><caption>Output</caption>…</listing> that follows
       a code listing — the output content is merged into the code listing.
    2. A <pre> block placed inside <listing> after </program> (old style) —
       converted to a <console> with <output> inside the listing.
    3. A <pre> block placed directly after </listing> (previous migration
       style) — converted to a <console> with <output> inside the listing.

    <figure> blocks placed after </listing> are left unchanged: figures
    cannot be children of <listing> in the PreTeXt schema.

    Returns True if any changes were made.
    """
    with open(ptx_path) as f:
        content = f.read()

    original = content

    # Case 1: merge a separate output listing into the preceding code listing.
    # Before: </program>\n    </listing>\n    <listing>\n      <caption>Output</caption>CONTENT</listing>
    # After:  </program>\n    </listing>CONTENT
    case1_pattern = re.compile(
        r"(</program>)"
        r"([ \t]*\n[ \t]*</listing>)"
        r"[ \t]*\n[ \t]*<listing>[ \t]*\n[ \t]*<caption>Output</caption>"
        r"(.*?)"
        r"[ \t]*\n[ \t]*</listing>",
        re.DOTALL,
    )
    content = case1_pattern.sub(r"\1\2\3", content)

    # Case 2: <pre> blocks inside <listing> after </program>
    # Before: <program>…</program>(pre_blocks)</listing>
    # After:  <console>…<output>…</output></console></listing>
    def _case2_replacer(m):
        return (
            _build_console_block(
                m.group(1), m.group(3), m.group(4), m.group(5), m.group(6),
                m.group(7),
            )
            + m.group(8)
        )

    case2_pattern = re.compile(
        _PROG_PAT
        + r"((?:[ \t]*\n[ \t]*<pre\b.*?</pre>)+)"
        r"([ \t]*\n[ \t]*</listing>)",
        re.DOTALL,
    )
    content = case2_pattern.sub(_case2_replacer, content)

    # Case 3: <pre> blocks directly after </listing>
    # Before: <program>…</program></listing>(pre_blocks)
    # After:  <console>…<output>…</output></console></listing>
    def _case3_replacer(m):
        return (
            _build_console_block(
                m.group(1), m.group(3), m.group(4), m.group(5), m.group(6),
                m.group(8),
            )
            + m.group(7)
        )

    case3_pattern = re.compile(
        _PROG_PAT
        + r"([ \t]*\n[ \t]*</listing>)"
        r"((?:[ \t]*\n[ \t]*<pre\b.*?</pre>)+)",
        re.DOTALL,
    )
    content = case3_pattern.sub(_case3_replacer, content)

    # Case 4: <console> blocks with boring <class '...'> output — revert to
    # <program> so that update_ptx_file can replace them with proper table output.
    content = clean_boring_console_outputs(content)

    if content != original:
        with open(ptx_path, "w") as f:
            f.write(content)
        print(f"  Migrated {ptx_path.name}: outputs converted to <console>")
        return True
    return False


def update_ptx_file(ptx_path, code_to_outputs):
    """
    Update a PTX source file by inserting output blocks for program listing
    blocks whose code matches a cell in the notebook.

    Text outputs are placed inside the <listing> by converting the <program>
    element to a <console> element with an <output> child.  Figure outputs
    are placed after </listing> because <figure> is not a valid child of
    <listing>.

    This function is idempotent:
    - Once a block is converted to <console> the program_pattern no longer
      matches it, so no duplicate output is inserted.
    - Figure outputs already present after </listing> are detected and
      skipped.

    Returns True if any changes were made.
    """
    with open(ptx_path) as f:
        content = f.read()

    # Pattern to match a <program language="python"> block (with leading
    # whitespace captured so we can reconstruct correct indentation).
    # Groups: (prog_indent)(open_tag)(input_open)(code)(input_close)(close_prog)
    program_pattern = re.compile(_PROG_PAT, re.DOTALL)

    # Pattern to detect an existing output block after the insertion point
    existing_output_pattern = re.compile(r"\s*<(pre|figure|table)\b", re.DOTALL)

    # Pattern to match a closing </listing> immediately after </program>
    listing_close_pattern = re.compile(r"(\s*</listing>)", re.DOTALL)

    changes_made = 0
    result_parts = []
    last_end = 0

    for match in program_pattern.finditer(content):
        prog_indent = match.group(1)
        input_open = match.group(3)
        code_raw = match.group(4)
        input_close = match.group(5)
        close_prog = match.group(6)

        after_program = content[match.end():]
        listing_close_match = listing_close_pattern.match(after_program)
        if not listing_close_match:
            # Not followed directly by </listing> — skip.
            result_parts.append(content[last_end : match.end()])
            last_end = match.end()
            continue

        listing_close_text = listing_close_match.group(1)
        listing_close_end = match.end() + listing_close_match.end()
        after_listing = content[listing_close_end:]
        already_has_post_output = bool(existing_output_pattern.match(after_listing))

        code_normalized = normalize_code(code_raw)
        outputs = code_to_outputs.get(code_normalized)

        if not outputs:
            result_parts.append(content[last_end:listing_close_end])
            last_end = listing_close_end
            continue

        pre_outputs = [o for o in outputs if "<pre>" in o]
        fig_outputs = [o for o in outputs if "<figure>" in o]
        table_outputs = [o for o in outputs if "<table>" in o]

        changes_made += 1

        if pre_outputs:
            # Convert <program> to <console> with <output> inside the listing.
            pre_str = "\n".join(pre_outputs)
            console_block = _build_console_block(
                prog_indent, input_open, code_raw, input_close, close_prog,
                pre_str,
            )
            result_parts.append(content[last_end : match.start()])
            result_parts.append(console_block)
            result_parts.append(listing_close_text)
        else:
            # No text output — keep <program> as-is and append up to </listing>.
            result_parts.append(content[last_end:listing_close_end])

        last_end = listing_close_end

        # Figure and table outputs always go after </listing>.
        if not already_has_post_output:
            post_outputs = fig_outputs + table_outputs
            if post_outputs:
                result_parts.append("\n" + "\n".join(post_outputs))

    result_parts.append(content[last_end:])
    new_content = "".join(result_parts)

    if changes_made > 0:
        with open(ptx_path, "w") as f:
            f.write(new_content)
        print(f"  Updated {ptx_path.name}: added {changes_made} output block(s)")
        return True
    else:
        print(f"  No changes: {ptx_path.name}")
        return False


def process_all_chapters():
    """Process all chapter PTX files and add outputs from notebooks."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    total_files = 0
    total_changed = 0

    for ptx_filename, notebook_path in CHAPTER_MAPPING.items():
        ptx_path = SOURCE_DIR / ptx_filename
        if not ptx_path.exists():
            print(f"SKIP (not found): {ptx_filename}")
            continue

        print(f"\nProcessing {ptx_filename}...")

        # Migrate any existing outputs that are outside their listing
        migrate_ptx_file(ptx_path)

        code_to_outputs = extract_outputs_from_notebook(notebook_path)

        if not code_to_outputs:
            print(f"  No outputs found in notebook")
            continue

        print(f"  Found {len(code_to_outputs)} cells with outputs")
        total_files += 1

        changed = update_ptx_file(ptx_path, code_to_outputs)
        if changed:
            total_changed += 1

    print(f"\nDone: {total_changed}/{total_files} files updated")


if __name__ == "__main__":
    print(f"Generating outputs from notebooks into PreTeXt source files...")
    print(f"Repo root: {REPO_ROOT}")
    print(f"Images will be saved to: {IMAGES_DIR}")
    process_all_chapters()
