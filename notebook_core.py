"""Shared nbformat scaffold for the _build_*.py notebook generators.

Every generator has the same shape: accumulate markdown/code cells, then write
a v4 notebook with the standard python3 kernelspec metadata. That repetition
lives here; each _build_*.py keeps only its notebook's content.
"""
import nbformat as nbf

METADATA = {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"}}


class NotebookBuilder:
    """Accumulates cells and writes the .ipynb. `strip=True` strips leading/trailing
    newlines from each cell source (the walkthrough's convention)."""

    def __init__(self, strip=False):
        self.cells = []
        self._strip = strip

    def md(self, src):
        if self._strip:
            src = src.strip("\n")
        self.cells.append(nbf.v4.new_markdown_cell(src))

    def co(self, src):
        if self._strip:
            src = src.strip("\n")
        self.cells.append(nbf.v4.new_code_cell(src))

    code = co  # walkthrough calls it `code`

    def write(self, path):
        nb = nbf.v4.new_notebook()
        nb["cells"] = self.cells
        nb["metadata"] = METADATA
        with open(path, "w", encoding="utf-8") as f:
            nbf.write(nb, f)
        print(f"wrote {path} with {len(self.cells)} cells")
