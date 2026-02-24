"""
Check that every code block in the PreTeXt source files exists verbatim
as a code cell in the corresponding Jupyter notebook.

This ensures the PreTeXt book and the Python (notebook) book stay in sync.
"""

import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

REPO_ROOT = Path(__file__).parent.parent
SOURCE_DIR = REPO_ROOT / "pretext" / "source"

# Map each PTX chapter file to its corresponding notebook
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
}


def extract_ptx_code_blocks(ptx_path):
    """Return a list of code strings from all Python code blocks.

    Extracts code from both <program language="python"> blocks and
    <console> blocks (which store code in <input> child elements).
    """
    tree = ElementTree.parse(ptx_path)
    root = tree.getroot()
    blocks = []
    for program in root.iter("program"):
        if program.get("language") != "python":
            continue
        input_elem = program.find("input")
        if input_elem is None or input_elem.text is None:
            continue
        # ElementTree already handles XML entity unescaping
        code = input_elem.text.strip()
        blocks.append(code)
    for console in root.iter("console"):
        input_elem = console.find("input")
        if input_elem is None or input_elem.text is None:
            continue
        code = input_elem.text.strip()
        blocks.append(code)
    return blocks


def extract_notebook_code_cells(nb_path):
    """Return a set of code strings from all code cells in the notebook."""
    with open(nb_path) as f:
        nb = json.load(f)
    cells = set()
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            code = "".join(cell.get("source", [])).strip()
            cells.add(code)
    return cells


@pytest.mark.parametrize("ptx_filename,nb_path", CHAPTER_MAPPING.items())
def test_ptx_code_blocks_match_notebook(ptx_filename, nb_path):
    """Every Python code block in the PTX file (in <program> or <console>
    elements) must exist verbatim as a code cell in the corresponding
    Jupyter notebook."""
    ptx_path = SOURCE_DIR / ptx_filename
    assert ptx_path.exists(), f"PTX file not found: {ptx_path}"
    assert nb_path.exists(), f"Notebook not found: {nb_path}"

    ptx_blocks = extract_ptx_code_blocks(ptx_path)
    nb_cells = extract_notebook_code_cells(nb_path)

    missing = [block for block in ptx_blocks if block not in nb_cells]
    assert missing == [], (
        f"{ptx_filename}: {len(missing)} PTX code block(s) not found verbatim "
        f"in {nb_path.name}:\n\n"
        + "\n\n---\n\n".join(repr(b) for b in missing)
    )
