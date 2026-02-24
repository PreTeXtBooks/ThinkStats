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


def migrate_ptx_file(ptx_path):
    """
    Migrate an existing PTX file so that output blocks are placed after
    their corresponding <listing> element rather than inside it.

    The PreTeXt schema only allows Program or Console inside <listing>,
    so <pre> and <figure> output blocks must appear after </listing>.

    Handles two cases:
    1. A separate <listing><caption>Output</caption>...</listing> that follows
       a code listing — the output content is moved outside the code listing.
    2. A <figure> or <pre> block placed inside <listing> after <program>
       (inserted by an older version of this script) — the block is moved
       to after </listing>.

    Returns True if any changes were made.
    """
    with open(ptx_path) as f:
        content = f.read()

    original = content

    # Case 1: merge a separate output listing into a block after the code listing.
    # Before: </program>\n    </listing>\n    <listing>\n      <caption>Output</caption>(content)</listing>
    # After:  </program>\n    </listing>(content)
    case1_pattern = re.compile(
        r"(</program>)"
        r"([ \t]*\n[ \t]*</listing>)"
        r"[ \t]*\n[ \t]*<listing>[ \t]*\n[ \t]*<caption>Output</caption>"
        r"(.*?)"
        r"[ \t]*\n[ \t]*</listing>",
        re.DOTALL,
    )
    content = case1_pattern.sub(r"\1\2\3", content)

    # Case 2: move figure/pre blocks from inside <listing> to after </listing>.
    # Before: </program>(output_blocks)\n    </listing>
    # After:  </program>\n    </listing>(output_blocks)
    case2_pattern = re.compile(
        r"(</program>)"
        r"((?:[ \t]*\n[ \t]*<(?:figure|pre)\b.*?</(?:figure|pre)>)+)"
        r"([ \t]*\n[ \t]*</listing>)",
        re.DOTALL,
    )
    content = case2_pattern.sub(r"\1\3\2", content)

    if content != original:
        with open(ptx_path, "w") as f:
            f.write(content)
        print(f"  Migrated {ptx_path.name}: outputs moved after listings")
        return True
    return False


def update_ptx_file(ptx_path, code_to_outputs):
    """
    Update a PTX source file by inserting output blocks after program
    listing blocks whose code matches a cell in the notebook.

    Output blocks (<pre> or <figure>) are placed after the closing
    </listing> tag, because the PreTeXt schema only allows Program or
    Console inside <listing>.

    This function is idempotent: it will not add outputs if they are
    already present after the listing.

    Returns True if any changes were made.
    """
    with open(ptx_path) as f:
        content = f.read()

    # Pattern to match a program block:
    # <program language="python">
    #   <input>
    # ... code ...
    #   </input>
    # </program>
    program_pattern = re.compile(
        r'(<program language="python">\s*<input>)(.*?)(</input>\s*</program>)',
        re.DOTALL,
    )

    # Pattern to detect an existing output block immediately after the insertion point
    existing_output_pattern = re.compile(
        r'\s*<(pre|figure)\b', re.DOTALL
    )

    # Pattern to match a closing </listing> tag immediately after </program>
    # (with only whitespace between). The group captures the full match so
    # its .end() gives the insertion point (right after </listing>).
    listing_close_pattern = re.compile(r'(\s*</listing>)', re.DOTALL)

    changes_made = 0
    result_parts = []
    last_end = 0

    for match in program_pattern.finditer(content):
        code_raw = match.group(2)

        # Find the </listing> that closes the listing containing this program.
        # If listing_close_pattern matches, only whitespace sits between
        # </program> and </listing>, so we insert AFTER </listing>.
        after_program = content[match.end():]
        listing_close_match = listing_close_pattern.match(after_program)
        if listing_close_match:
            # Insert output after </listing>
            insertion_end = match.end() + listing_close_match.end()
            # Check if there's already output right after </listing>
            after_listing = content[insertion_end:]
            already_has_output = bool(existing_output_pattern.match(after_listing))
        else:
            # Program is not in a simple listing or listing already has other
            # content — skip (do not insert output here).
            result_parts.append(content[last_end:match.end()])
            last_end = match.end()
            continue

        code_normalized = normalize_code(code_raw)
        outputs = code_to_outputs.get(code_normalized)

        result_parts.append(content[last_end:insertion_end])
        last_end = insertion_end

        if outputs and not already_has_output:
            changes_made += 1
            output_xml = "\n" + "\n".join(outputs)
            result_parts.append(output_xml)

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
