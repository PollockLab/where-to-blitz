"""The app backtest must use the viewer's lattice, inventory and numeric blends."""

import ast
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pyproj import CRS, Transformer

import backtest_appscore as bas
import grid_lattice
import voi_backtest as vb
from goal_presets import AXES, PRESETS

ROOT = Path(__file__).resolve().parent
INDEX = json.loads((ROOT / "cluster_results/ca/index.json").read_text())
CRS_APP = CRS.from_user_input(INDEX["crs"])
INVERSE = Transformer.from_crs(CRS_APP, "EPSG:4326", always_xy=True)


def geographic_point(row, col, dx=12500, dy=12500):
    lon, lat = INVERSE.transform(
        INDEX["lattice"]["x0"] + col * 25000 + dx, INDEX["lattice"]["y1"] - row * 25000 - dy
    )
    return lat, lon


@pytest.fixture
def app_dir(tmp_path):
    rows = []
    for i in range(12):
        lat, lon = geographic_point(80 + i, 40)
        rows.append([round(lat, 3), round(lon, 3), 0.2 + i * 0.05, 0.1, 0.4, 0.3, 0.2, 30, 20])
    idx = dict(INDEX, n_cells=len(rows), files={"Amphibia": "cells.json"})
    (tmp_path / "index.json").write_text(json.dumps(idx))
    (tmp_path / "cells.json").write_text(json.dumps({"Amphibia": rows}))
    (tmp_path / "us_cells.json").write_text(json.dumps({"us_cells": []}))
    return tmp_path


def observation_frame():
    rows = []
    for i in range(12):
        lat, lon = geographic_point(80 + i, 40)
        for j in range(i + 1):
            rows.append([lat, lon, "2025-06-01", 100, "species"])
        for j in range(6):
            rows.append([lat, lon, "2025-08-01", 200 + j, "species"])
    return pd.DataFrame(rows, columns=["lat", "lon", "observed_on", "taxon_id", "rank"])


def axis_frame():
    return pd.DataFrame(
        {
            "app_discover": [0.2, 0.6],
            "app_conservation": [0.4, 0.7],
            "app_env": [0.5, 0.9],
            "app_staleness": [0.3, 0.4],
            "app_urgency": [0.1, 0.2],
            "scarcity": [0.25, 0.5],
        }
    )


def test_mixed_composite_matches_browser_impact():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to execute the viewer blend")
    source = (ROOT / "webapp/index.html").read_text()
    impact = re.search(r"function impact\(r\)\{[^\n]+\}", source).group()
    index_positions = source[source.index("const IDX=") : source.index("const OSRM_BASE=")]
    builder = ast.parse((ROOT / "build_webapp.py").read_text())
    objectives = ast.literal_eval(
        next(
            node.value
            for node in builder.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "OBJ" for target in node.targets)
        )
    )
    frame = axis_frame()
    rows = [[45, -75] + [float(row["app_" + ax]) for ax in AXES] for _, row in frame.iterrows()]
    for preset in PRESETS:
        script = (
            "const state={w:" + json.dumps(dict(zip(AXES, preset["w"]))) + "};"
            "const OBJ="
            + json.dumps(objectives)
            + ";"
            + index_positions
            + impact
            + "console.log(JSON.stringify("
            + json.dumps(rows)
            + ".map(impact)));"
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        np.testing.assert_allclose(
            bas.composite(frame, preset["w"], "app_discover"), json.loads(result.stdout)
        )


def test_composite_does_not_change_when_unrelated_cells_extend_the_dataset():
    frame = axis_frame()
    more = pd.concat([frame, pd.DataFrame([{col: 1 for col in frame.columns}])], ignore_index=True)
    weights = [1, 0, 0, 0.6, 0]
    np.testing.assert_allclose(
        bas.composite(frame, weights, "app_discover"),
        bas.composite(more, weights, "app_discover").iloc[: len(frame)],
    )
    np.testing.assert_allclose(bas.composite(frame, [1, 0, 0, 0, 0], "scarcity"), frame.scarcity)


def test_inventory_and_scores_are_independent_of_source_row_order(app_dir):
    grid = bas.AppGrid("Amphibia", app_dir)
    original = bas.load_app_axes("Amphibia", grid)
    path = app_dir / "cells.json"
    rows = json.loads(path.read_text())["Amphibia"]
    path.write_text(json.dumps({"Amphibia": rows[::-1]}))
    reordered = bas.AppGrid("Amphibia", app_dir)
    assert bas.load_app_axes("Amphibia", reordered) == original
    assert reordered.metadata["inventory_sha256"] == grid.metadata["inventory_sha256"]


def test_duplicate_cells_fail_instead_of_overwriting_scores(app_dir):
    path = app_dir / "cells.json"
    rows = json.loads(path.read_text())["Amphibia"]
    rows[-1] = rows[0]
    path.write_text(json.dumps({"Amphibia": rows}))
    with pytest.raises(ValueError, match="duplicate"):
        bas.AppGrid("Amphibia", app_dir)


@pytest.mark.parametrize(
    "change", [{"res_m": 5000}, {"crs": "EPSG:4326"}, {"lattice": {"x0": 0}}, {"n_cells": 999}]
)
def test_unknown_or_mismatched_grid_metadata_fails(app_dir, change):
    path = app_dir / "index.json"
    path.write_text(json.dumps(dict(json.loads(path.read_text()), **change)))
    with pytest.raises(ValueError):
        bas.AppGrid("Amphibia", app_dir)


def test_observations_use_exact_projected_cells_and_preserve_legacy_default(app_dir):
    grid = bas.AppGrid("Amphibia", app_dir)
    df = observation_frame()
    cells = vb.build_cells(df, grid=grid)
    assert list(zip(cells.gi, cells.gj)) == [(80 + i, 40) for i in range(12)]
    np.testing.assert_equal(cells.n_train, np.arange(1, 13))
    assert cells.new_species.tolist() == [6] * 12
    assert cells.attrs["grid"] == grid.metadata
    legacy = vb.build_cells(df)
    assert set(zip(legacy.gi, legacy.gj)) == {
        (int(np.floor(r.lat / vb.RES)), int(np.floor(r.lon / vb.RES))) for r in df.itertuples()
    }


def test_unpublished_foreign_null_and_off_grid_points_are_not_eligible(app_dir):
    path = app_dir / "cells.json"
    row = json.loads(path.read_text())["Amphibia"][0]
    (app_dir / "us_cells.json").write_text(json.dumps({"us_cells": [f"{row[0]:.3f},{row[1]:.3f}"]}))
    grid = bas.AppGrid("Amphibia", app_dir)
    points = [
        geographic_point(80, 40),
        geographic_point(81, 40),
        geographic_point(81, 41),
        geographic_point(-1, 40),
        (np.nan, -75),
        (95, -75),
    ]
    rows, cols = grid.locate(*map(np.array, zip(*points)))
    assert list(zip(rows, cols)) == [(-1, -1), (81, 40), (-1, -1), (-1, -1), (-1, -1), (-1, -1)]


def test_cell_assignment_matches_browser_snap_at_near_boundaries(app_dir):
    if not shutil.which("node"):
        pytest.skip("Node.js is required to execute browser cell assignment")
    grid = bas.AppGrid("Amphibia", app_dir)
    source = (ROOT / "webapp/index.html").read_text()
    laea = re.search(r"const LAEA=\(\(\)=>\{.*?\n\}\)\(\);", source, re.S).group()
    points = [geographic_point(80, 40, dx=dx, dy=dy) for dx in [1, 24999] for dy in [1, 24999]]
    script = (
        laea + "const pts=" + json.dumps(points) + ";console.log(JSON.stringify(pts.map(p=>{"
        f"const c=LAEA.fwd(p[0],p[1]);return [Math.floor(({grid_lattice.Y1}-c[1])/25000),"
        f"Math.floor((c[0]-({grid_lattice.X0}))/25000)];" + "})));"
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    rows, cols = grid.locate(*map(np.array, zip(*points)))
    assert list(map(list, zip(rows, cols))) == json.loads(result.stdout)


def test_post_split_labels_cannot_change_train_only_inputs(app_dir):
    grid = bas.AppGrid("Amphibia", app_dir)
    df = observation_frame()
    changed = df.copy()
    changed.loc[changed.observed_on >= "2025-07-01", "taxon_id"] = 999
    before = vb.build_cells(df, grid=grid)
    after = vb.build_cells(changed, grid=grid)
    for col in ["n_train", "scarcity", "staleness", "priority", "chao_unseen"]:
        np.testing.assert_equal(before[col].to_numpy(), after[col].to_numpy())


def test_historical_axes_never_receive_a_leakfree_label(app_dir, monkeypatch):
    monkeypatch.setattr(
        bas,
        "PRESETS",
        [
            {"name": "Climate candidate", "w": [1, 0, 1, 0, 0]},
            {"name": "Conservation", "w": [0, 1, 0, 0, 0.4]},
            {"name": "Species discovery", "w": [1, 0, 0, 0.6, 0]},
        ],
    )
    monkeypatch.setattr(vb, "N_PERM", 5)
    out, _ = bas.analyse("Amphibia", observation_frame(), data_dir=app_dir)
    scores = out["scores"]
    assert "app_leakfree" in scores
    assert "climate_candidate_shipped" in scores and "climate_candidate_leakfree" not in scores
    assert "conservation_shipped" in scores and "conservation_leakfree" not in scores
    assert "species_discovery_shipped" in scores and "species_discovery_leakfree" not in scores
    assert out["run"]["grid"]["res_m"] == 25000
    assert out["run"]["split"] == "2025-07-01"


def test_identification_backlog_caller_can_use_the_same_explicit_grid(app_dir, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    spec = importlib.util.spec_from_file_location("idlatency", ROOT / "research/exp_idlatency.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    grid = bas.AppGrid("Amphibia", app_dir)
    rows = module.grid(observation_frame(), lattice=grid)
    assert set(zip(rows.gi, rows.gj)) == {(80 + i, 40) for i in range(12)}


def test_empty_coordinates_and_empty_observations_have_no_cells(app_dir):
    grid = bas.AppGrid("Amphibia", app_dir)
    rows, cols = grid.locate([], [])
    assert len(rows) == len(cols) == 0
    assert vb.build_cells(observation_frame().iloc[:0], grid=grid) is None


def test_legacy_cells_cannot_be_joined_to_projected_app_axes(app_dir):
    grid = bas.AppGrid("Amphibia", app_dir)
    with pytest.raises(ValueError, match="explicit app grid"):
        bas.join_axes(vb.build_cells(observation_frame()), bas.load_app_axes("Amphibia", grid))
    with pytest.raises(ValueError, match="group"):
        bas.load_app_axes("Aves", grid)


def test_same_degree_bin_can_hold_two_independent_app_cells(app_dir):
    data = json.loads((ROOT / "cluster_results/ca/webapp_data_Amphibia.json").read_text())["Amphibia"]
    bins = {}
    pair = None
    for row in data:
        key = (int(np.floor(row[0] / 0.25)), int(np.floor(row[1] / 0.25)))
        if key in bins:
            pair = [bins[key], row]
            break
        bins[key] = row
    assert pair is not None
    pair[0][2], pair[1][2] = 0.1, 0.9
    path = app_dir / "index.json"
    path.write_text(json.dumps(dict(json.loads(path.read_text()), n_cells=2)))
    (app_dir / "cells.json").write_text(json.dumps({"Amphibia": pair}))
    grid = bas.AppGrid("Amphibia", app_dir)
    assert len(grid.axes) == 2
    assert sorted(axes["discover"] for axes in grid.axes.values()) == [0.1, 0.9]


def test_input_row_order_cannot_change_cell_membership_or_rarefaction(app_dir):
    grid = bas.AppGrid("Amphibia", app_dir)
    frame = observation_frame()
    frame.loc[(frame.observed_on >= "2025-07-01") & (frame.index % 3 == 0), "taxon_id"] = 100
    before = vb.build_cells(frame, grid=grid)
    after = vb.build_cells(frame.sample(frac=1, random_state=7), grid=grid)
    assert before.attrs["aux"] == after.attrs["aux"]
    np.testing.assert_equal(
        vb.rarefy_new_at_k(before, before.attrs["aux"], np.random.default_rng(0)),
        vb.rarefy_new_at_k(after, after.attrs["aux"], np.random.default_rng(0)),
    )


def test_runtime_permutation_count_is_passed_and_reflected_in_metadata(app_dir, monkeypatch):
    monkeypatch.setattr(vb, "N_PERM", 7)
    original = vb.perm_test
    counts = []
    def measured(score, outcome, rng, n=None):
        counts.append(n)
        return original(score, outcome, rng, n=n)
    monkeypatch.setattr(vb, "perm_test", measured)
    out, _ = bas.analyse("Amphibia", observation_frame(), data_dir=app_dir)
    assert counts and counts == [7] * len(out["scores"])
    assert out["run"]["n_permutations"] == 7
    assert out["perm_p_floor"] == 1 / 8
    assert out["run"]["permutation_probability"] == "(exceedances + 1) / (draws + 1)"


def test_outcome_and_retrospective_limits_are_recorded(app_dir, monkeypatch):
    monkeypatch.setattr(vb, "N_PERM", 3)
    out, _ = bas.analyse("Amphibia", observation_frame(), data_dir=app_dir)
    run = out["run"]
    assert run["outcome"]["unit"] == "iNaturalist taxon_id"
    assert run["outcome"]["baseline"] == "sampled project observations with observed_on before split"
    assert run["outcome"]["ranks"] == ["species", "subspecies", "variety", "form", "hybrid"]
    assert run["limitations"]["historical_availability"] == "unknown"
    assert run["limitations"]["previously_unobserved_cells"] == "excluded"
    assert run["limitations"]["effort_unit"] == "observation count, not field hours"
    assert run["limitations"]["multiple_comparisons"] == "unadjusted"
    assert "certified" not in json.dumps(run)


def test_app_defaults_to_exact_expectation_and_retains_historical_mc_option(app_dir, monkeypatch):
    monkeypatch.setattr(vb, "N_PERM", 3)
    frame = observation_frame()
    exact, cells = bas.analyse("Amphibia", frame, data_dir=app_dir)
    assert exact["run"]["rarefaction_method"] == "exact hypergeometric expectation"
    assert exact["run"]["rarefaction_reps"] is None
    assert exact["run"]["rarefaction_seed"] is None
    np.testing.assert_allclose(cells.rare_newK.to_numpy(), 5, rtol=0, atol=1e-12)
    sampled, _ = bas.analyse("Amphibia", frame, data_dir=app_dir, outcome_method="monte_carlo")
    assert sampled["run"]["rarefaction_reps"] == 200
    assert sampled["run"]["rarefaction_seed"] == vb.SEED
    with pytest.raises(ValueError, match="outcome_method"):
        bas.analyse("Amphibia", frame, data_dir=app_dir, outcome_method="bad")


def test_training_history_sensitivity_uses_same_cohort(app_dir, monkeypatch):
    monkeypatch.setattr(vb, "N_PERM", 7)
    rows=[]
    for i in range(12):
        lat,lon=geographic_point(80+i,40)
        nt=10 if i<2 else 20+i
        for j in range(nt):
            rows.append([lat,lon,"2025-06-01",100+j % (i+1),"species"])
        for j in range(12):
            rows.append([lat,lon,"2025-08-01",100+j % 6,"species"])
    frame=pd.DataFrame(rows,columns=["lat","lon","observed_on","taxon_id","rank"])
    out,rk=bas.analyse("Amphibia",frame,data_dir=app_dir)
    sensitivity=out["training_history_sensitivity"]
    assert out["n_rarefied"]==12
    assert sensitivity["M"]==20 and sensitivity["K"]==5
    assert sensitivity["n_cells"]==10
    grid=bas.AppGrid("Amphibia",app_dir)
    aux=vb.build_cells(frame,grid=grid).attrs["aux"]
    paired=rk[rk.n_train>=20]
    standardized=vb.expected_standardized_new_at_k(paired,aux,M=20,K=5)
    assert sensitivity["mean_full_training"]==pytest.approx(paired.rare_newK.mean())
    assert sensitivity["mean_standardized_training"]==pytest.approx(standardized.mean())
    for key,score in [("discover_leakfree",paired.scarcity),("app_shipped",paired.app_shipped)]:
        record=sensitivity["paired_scores"][key]
        for label,values in [("full_training",paired.rare_newK),("standardized_training",standardized)]:
            rho,p,*_=vb.perm_test(score.to_numpy(),np.asarray(values),np.random.default_rng(7),n=7)
            assert record[label]["spearman"]==pytest.approx(rho,nan_ok=True)
            assert record[label]["perm_p"]==pytest.approx(p,nan_ok=True)
    assert len(sensitivity["cohort_keys_sha256"])==64


def test_unavailable_training_history_cohort_is_explicit(app_dir,monkeypatch):
    monkeypatch.setattr(vb,"N_PERM",3)
    out,_=bas.analyse("Amphibia",observation_frame(),data_dir=app_dir)
    sensitivity=out["training_history_sensitivity"]
    assert sensitivity["n_cells"]==0
    assert sensitivity["paired_scores"]=={}
    assert sensitivity["mean_full_training"] is None
    assert sensitivity["mean_standardized_training"] is None
    assert "fewer than 10" in sensitivity["unavailable_reason"]
