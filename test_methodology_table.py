"""Gate the METHODOLOGY validation table against the result files it claims to read.

METHODOLOGY.md publishes a per-taxon table of backtest numbers and tells the reader it
"reads straight from a committed result file". Nothing enforced that. The table sat for
months quoting a composite (`discover` 0.8 + `env` 0.7 + `urgency` 0.3) that no shipped
preset had used since `backtest_appscore.py` started reading `goal_presets.PRESETS`, and
rerunning the backtest moved every cell count and every rho without touching the prose.

These tests parse the markdown table and compare it to the two JSONs. Rounding is the
table's own: rho to two decimals, yield to one. A rerun that shifts a number now fails
here instead of quietly making the document wrong.
"""

import json
import re
from pathlib import Path

import pytest

HERE = Path(__file__).parent
DOC = HERE / "METHODOLOGY.md"
RESULTS = {
    "BC": HERE / "cluster_results" / "voi_appscore_results.json",
    "East": HERE / "cluster_results" / "voi_appscore_east_results.json",
}
# table label -> backtest taxon key
TAXON = {"Amphibians": "Amphibia", "Birds": "Aves", "Insects": "Insecta",
         "Mammals": "Mammalia", "Reptiles": "Reptilia"}
NS_P = 0.05          # the table's `n.s.` threshold
ROW = re.compile(
    r"^\|\s*(?P<taxon>[A-Za-z]+)\s*\|\s*(?P<region>BC|East)\s*\|\s*(?P<n>\d+)\s*\|"
    r"\s*(?P<leakfree>-?[\d.]+)\s*\|\s*(?P<shipped>-?[\d.]+)(?P<ns>\s*n\.s\.)?\s*\|"
    r"\s*(?P<yield_>[\d.]+)x\s*\|\s*$")

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in RESULTS.values()),
    reason="no committed backtest results present")


def _scores():
    """(region, taxon) -> the score dict the table quotes."""
    out = {}
    for region, path in RESULTS.items():
        for rec in json.loads(path.read_text()):
            out[(region, rec["taxon"])] = rec
    return out


def _rows():
    rows = [m.groupdict() for line in DOC.read_text().splitlines()
            if (m := ROW.match(line))]
    assert rows, "no validation rows parsed out of METHODOLOGY.md"
    return rows


def test_every_result_taxon_has_a_table_row():
    published = {(r["region"], TAXON[r["taxon"]]) for r in _rows()}
    assert published == set(_scores()), "table and result files disagree on which taxa exist"


@pytest.mark.parametrize("row", _rows(), ids=lambda r: f'{r["region"]}-{r["taxon"]}')
def test_row_matches_the_committed_result(row):
    rec = _scores()[(row["region"], TAXON[row["taxon"]])]
    s = rec["scores"]
    assert int(row["n"]) == rec["n_rarefied"]
    assert float(row["leakfree"]) == round(s["app_leakfree"]["spearman"], 2)
    assert float(row["shipped"]) == round(s["app_shipped"]["spearman"], 2)
    assert float(row["yield_"]) == round(s["app_leakfree"]["eff_ratio_top_bottom"], 1)
    # `n.s.` is a claim about the permutation p, not about the rho being small
    flagged = row["ns"] is not None
    assert flagged == (s["app_shipped"]["perm_p"] > NS_P)


def test_headline_range_covers_every_row():
    """The prose quotes one rho range and one yield range. Both must bracket the table."""
    doc = DOC.read_text()
    rho = [s["scores"]["app_leakfree"]["spearman"] for s in _scores().values()]
    ratio = [s["scores"]["app_leakfree"]["eff_ratio_top_bottom"] for s in _scores().values()]
    assert f"rho {min(rho):.2f} to {max(rho):.2f}" in doc
    assert f"{min(ratio):.1f}x to {max(ratio):.1f}x" in doc


def test_composite_column_would_be_a_duplicate():
    """The dropped Composite column: the default preset is discover-only, so app_leakfree
    IS discover_leakfree. If a preset change ever breaks that, the table needs the column
    back and this test says so."""
    for key, rec in _scores().items():
        s = rec["scores"]
        assert s["app_leakfree"]["spearman"] == s["discover_leakfree"]["spearman"], key
