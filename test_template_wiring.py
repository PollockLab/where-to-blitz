"""The inline script and the markup it manipulates must agree on what exists.

W5, W6 and W7 were all the same failure: the script referenced an element id, or a
data-i18n* key, that the markup never provided (or never routed to a live node), so a
string built at init or at runtime landed nowhere a reader could see. These checks read
the template as text, the same way the browser eventually parses it, and catch that drift
before a build ships it.
"""

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "webapp" / "index.html"

# ids the script assigns to elements it creates itself (createElement(...) then .id = 'x'),
# which the static markup will never contain and should not be expected to.
RUNTIME_IDS = {
    "popgaps",  # exploreCell: popGaps=document.createElement('div'); popGaps.id='popgaps'
    "popsp",  # exploreCell: popSp=document.createElement('div'); popSp.id='popsp'
}

# lookups that are real, pre-existing dead code (not the strand-a-string bug this file
# guards against): each is a guarded getElementById() whose element was deliberately
# removed from the markup, so the guard just makes the listener a permanent no-op.
KNOWN_DEAD_LOOKUPS = {
    "tgCanadaOnly",  # index.html:778 comment: "issue #20 removed the toggle" from markup
}


def _strip_script(src):
    """The document with every <script>...</script> block removed -- what a reader's DOM
    is built from before any JS runs."""
    return re.sub(r"<script\b[^>]*>.*?</script>", "", src, flags=re.S)


def _referenced_ids(src):
    """Every id looked up via getElementById('x'), querySelector('#x') or
    querySelectorAll('#x') anywhere in the file (these calls only live in <script>)."""
    ids = set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", src))
    ids |= set(re.findall(r"querySelector(?:All)?\('#([A-Za-z0-9_-]+)'\)", src))
    return ids


def _runtime_created_ids(src):
    """ids the script hands to elements it builds itself: any `.id='x'` (or `.id="x"`)
    assignment, plus the short explicit allowlist above for anything that regex misses."""
    return set(re.findall(r"\.id\s*=\s*['\"]([A-Za-z0-9_-]+)['\"]", src)) | RUNTIME_IDS


def _markup_ids(src):
    """Every id="x" attribute in the static markup (script blocks excluded)."""
    return set(re.findall(r'id="([A-Za-z0-9_-]+)"', _strip_script(src)))


def _script_text(src):
    """The raw text inside the app's inline <script> block (the longest one -- the two
    `<script src=...>` CDN tags have no inline body)."""
    blocks = re.findall(r"<script\b[^>]*>(.*?)</script>", src, flags=re.S)
    return max(blocks, key=len) if blocks else ""


def _i18n_segments(src):
    """The source text of I18N.en and of I18N.fr."""
    i = src.index("const I18N")
    depth = 0
    for n, ch in enumerate(src[i:]):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0 and n > 10:
            seg = src[i : i + n]
            break
    return seg[seg.index("en:{") : seg.index("fr:{")], seg[seg.index("fr:{") :]


def _i18n_keys(seg):
    # keys start a line or follow a comma; both forms occur in the dict
    return set(re.findall(r"(?:\n\s+|, )([a-z0-9_]+):", seg))


def _i18n_value(seg, key):
    """The raw string literal I18N[lang][key] is assigned, template-literal or plain."""
    m = re.search(re.escape(key) + r":`((?:[^`\\]|\\.)*)`", seg)
    if m:
        return m.group(1)
    m = re.search(re.escape(key) + r':"((?:[^"\\]|\\.)*)"', seg)
    return m.group(1) if m else ""


def _ids_from_wired_i18n_html(src, en_seg, fr_seg):
    """ids that only appear once a data-i18n-html node pulls that key's string in as
    innerHTML -- e.g. <span id="legendtap"> living inside I18N.legend_hint, reachable only
    because some node in the markup carries data-i18n-html="legend_hint"."""
    wired_keys = set(re.findall(r'data-i18n-html="([a-z0-9_]+)"', _strip_script(src)))
    ids = set()
    for key in wired_keys:
        for seg in (en_seg, fr_seg):
            ids |= set(re.findall(r'id="([A-Za-z0-9_-]+)"', _i18n_value(seg, key)))
    return ids


def _ids_from_script_html_strings(src, en_seg, fr_seg):
    """ids embedded in HTML string literals the script builds and inserts itself in the
    same function (e.g. renderInsights' `id="idis"` div, assigned via `ins.innerHTML=html`
    a few lines later) -- unlike I18N values, these need no separate wiring step, so they
    are as good as markup. The I18N dictionary text is excluded: its ids are only live once
    something wires them in, handled by _ids_from_wired_i18n_html above."""
    script_minus_i18n = _script_text(src).replace(en_seg, "").replace(fr_seg, "")
    return set(re.findall(r'id="([A-Za-z0-9_-]+)"', script_minus_i18n))


def check_ids_wired(src):
    """Return the ids the script looks up that nothing in the markup ever provides."""
    en_seg, fr_seg = _i18n_segments(src)
    provided = (
        _markup_ids(src)
        | _runtime_created_ids(src)
        | _ids_from_wired_i18n_html(src, en_seg, fr_seg)
        | _ids_from_script_html_strings(src, en_seg, fr_seg)
        | KNOWN_DEAD_LOOKUPS
    )
    return _referenced_ids(src) - provided


def test_every_referenced_id_exists_in_the_markup():
    missing = check_ids_wired(TEMPLATE.read_text())
    assert not missing, (
        f"script looks up ids the markup never provides (or never routes into the DOM): {sorted(missing)}"
    )


I18N_ATTR = re.compile(r'data-i18n(?:-html|-aria|-title)?="([a-z0-9_]+)"')


def check_i18n_attrs_wired(src):
    """Return the data-i18n / data-i18n-html / data-i18n-aria / data-i18n-title keys the
    markup asks for that are missing from I18N.en or I18N.fr (applyI18N overwrites these
    nodes at init; a missing key leaves the node blank or literal in one language)."""
    en_seg, fr_seg = _i18n_segments(src)
    en, fr = _i18n_keys(en_seg), _i18n_keys(fr_seg)
    used = set(I18N_ATTR.findall(_strip_script(src)))
    return {k for k in used if k not in en or k not in fr}


def test_every_i18n_attribute_key_exists_in_both_languages():
    missing = check_i18n_attrs_wired(TEMPLATE.read_text())
    assert not missing, f"markup asks for i18n keys absent from en or fr: {sorted(missing)}"


def _fn_body(src, name):
    """Source of `function name(...){...}` up to the next top-level function declaration."""
    m = re.search(r"\n(?:async )?function " + name + r"\(.*?(?=\n(?:async )?function |\Z)", src, flags=re.S)
    assert m, f"{name} not found in the template"
    return m.group(0)


def test_explore_cell_opens_every_drawn_5km_cell():
    """A tap opens the lattice cell under it: 5 km cells are gated on DRAWN5 (the cells
    refreshCells5 drew), not a degree distance to the nearest 25 km centre, which rejected
    most off-centre 5 km cells because a degree of longitude shrinks northward."""
    src = TEMPLATE.read_text()
    body = _fn_body(src, "exploreCell")
    assert "maxTap" not in body and "nearestMarker" not in body
    assert "DRAWN5.has(latKey(snap))" in body and "parentMarker(" in body
    assert "DRAWN5=new Set(" in _fn_body(src, "refreshCells5")


def test_no_score_popup_line_exists_in_both_languages():
    en_seg, fr_seg = _i18n_segments(TEMPLATE.read_text())
    assert "pop_noscore" in _i18n_keys(en_seg) and "pop_noscore" in _i18n_keys(fr_seg)


def test_neighbourhood_queries_are_cut_to_the_cells_surface():
    """Both ~0.5° neighbourhood queries (species list and coverage tree) wait on
    surfaceFilter, which reads the build's land/sea mask, so a land cell is not offered
    whales from the coast and a sea cell is not offered moose from the shore."""
    src = TEMPLATE.read_text()
    body = _fn_body(src, "surfaceFilter")
    assert "land_sea_5000m.png" in body
    assert "'&taxon_id=':'&without_taxon_id='" in body
    assert "(await mxP)" in _fn_body(src, "fetchProspects")
    assert "surfaceFilter(lat,lon,resM||25000).then(" in _fn_body(src, "fetchGapTree")
