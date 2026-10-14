"""Replay the eight frozen sampled-record benchmarks without replacing their results."""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import backtest_appscore as bas

ROOT = Path(__file__).resolve().parent
RELEASE = "https://github.com/PollockLab/where-to-blitz/releases/download/grid-inputs-v1"
CASES = {
    "BC": ("Amphibia", "Aves", "Insecta", "Mammalia", "Reptilia"),
    "East": ("Aves", "Insecta", "Mammalia"),
}
RESULT_FILES = {"BC": "voi_appscore_results.json", "East": "voi_appscore_east_results.json"}
SOURCE_FILES = (
    "backtest_appscore.py",
    "voi_backtest.py",
    "goal_presets.py",
    "grid_lattice.py",
    "grid_schema.py",
)
SCORES = (
    "app_shipped",
    "discover_leakfree",
    "env",
    "urgency",
    "opportunistic_density",
    "app_leakfree",
    "species_discovery_shipped",
    "conservation_shipped",
)
VERSION_KEYS = ("python", "numpy", "pandas", "pyproj", "PROJ")


class VerificationError(ValueError):
    """A manifest, provenance or replay comparison failed."""


@dataclass(frozen=True)
class BenchmarkCase:
    region: str
    taxon: str
    expected: dict

    @property
    def filename(self):
        prefix = "inat_" if self.region == "BC" else "inat_east_"
        return f"{prefix}{self.taxon}.csv"

    @property
    def path(self):
        return f"$[{self.region}/{self.taxon}]"


def _fail(path, message):
    raise VerificationError(f"{path}: {message}")


def _keys(value, required, path, optional=()):
    if type(value) is not dict:
        _fail(path, "expected JSON object")
    missing, extra = set(required) - value.keys(), value.keys() - set(required) - set(optional)
    if missing or extra:
        _fail(path, f"keys differ (missing={sorted(missing)}, extra={sorted(extra)})")


def _string(value, path):
    if type(value) is not str or not value:
        _fail(path, "expected nonempty string")


def _integer(value, path, minimum=0):
    if type(value) is not int or value < minimum:
        _fail(path, f"expected integer >= {minimum}")


def _number(value, path, minimum=None, maximum=None):
    if value is None:
        return
    if type(value) is not float or not math.isfinite(value):
        _fail(path, "expected finite JSON float or null")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        _fail(path, "numeric value outside permitted range")


def _literal(value, expected, path):
    if type(value) is not type(expected) or value != expected:
        _fail(path, f"frozen configuration must be {expected!r}")


def _hashes(value, names, path):
    _keys(value, names, path)
    for name, digest in value.items():
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            _fail(f"{path}.{name}", "expected lowercase SHA-256 digest")


def _versions(value, path):
    _keys(value, VERSION_KEYS, path)
    for name, version in value.items():
        _string(version, f"{path}.{name}")


def validate_result(result, path):
    """Reject incomplete or incompatible benchmark manifests before acquiring inputs."""
    _keys(
        result,
        (
            "taxon",
            "region",
            "n_cells",
            "n_rarefied",
            "K",
            "perm_p_floor",
            "total_new_species",
            "scores",
            "training_history_sensitivity",
            "run",
        ),
        path,
    )
    for name in ("taxon", "region"):
        _string(result[name], f"{path}.{name}")
    for name in ("n_cells", "n_rarefied", "total_new_species"):
        _integer(result[name], f"{path}.{name}")
    if not 10 <= result["n_rarefied"] <= result["n_cells"]:
        _fail(f"{path}.n_rarefied", "must be between 10 and n_cells")
    _literal(result["K"], 5, f"{path}.K")
    _literal(result["perm_p_floor"], 1.0 / 2001, f"{path}.perm_p_floor")
    scores = result["scores"]
    _keys(scores, SCORES, f"{path}.scores")
    for name, score in scores.items():
        p = f"{path}.scores.{name}"
        _keys(score, ("spearman", "perm_p", "eff_ratio_top_bottom", "new_at_K_top", "new_at_K_bottom"), p)
        _number(score["spearman"], f"{p}.spearman", -1, 1)
        _number(score["perm_p"], f"{p}.perm_p", 1.0 / 2001, 1)
        _number(score["eff_ratio_top_bottom"], f"{p}.eff_ratio_top_bottom", 0)
        for name in ("new_at_K_top", "new_at_K_bottom"):
            _number(score[name], f"{p}.{name}", 0, 5)

    sensitivity = result["training_history_sensitivity"]
    p = f"{path}.training_history_sensitivity"
    _keys(
        sensitivity,
        (
            "M",
            "K",
            "n_cells",
            "method",
            "cohort",
            "cohort_keys_sha256",
            "estimand",
            "interpretation",
            "mean_full_training",
            "mean_standardized_training",
            "paired_scores",
            "permutation_seed",
            "n_permutations",
        ),
        p,
        optional=("unavailable_reason",),
    )
    for name, expected in (
        ("M", 20),
        ("K", 5),
        ("permutation_seed", 7),
        ("n_permutations", 2000),
        ("method", "exact independent train/test hypergeometric expectation"),
    ):
        _literal(sensitivity[name], expected, f"{p}.{name}")
    _integer(sensitivity["n_cells"], f"{p}.n_cells")
    if sensitivity["n_cells"] > result["n_rarefied"]:
        _fail(f"{p}.n_cells", "exceeds n_rarefied")
    for name in ("method", "cohort", "estimand", "interpretation"):
        _string(sensitivity[name], f"{p}.{name}")
    _hashes({"cohort": sensitivity["cohort_keys_sha256"]}, ("cohort",), f"{p}.cohort_keys_sha256")
    for name in ("mean_full_training", "mean_standardized_training"):
        _number(sensitivity[name], f"{p}.{name}", 0, 5)
    if sensitivity["n_cells"] < 10:
        _keys(sensitivity["paired_scores"], (), f"{p}.paired_scores")
        _string(sensitivity.get("unavailable_reason"), f"{p}.unavailable_reason")
    else:
        if "unavailable_reason" in sensitivity:
            _fail(f"{p}.unavailable_reason", "unexpected for an available paired cohort")
        _keys(sensitivity["paired_scores"], ("discover_leakfree", "app_shipped"), f"{p}.paired_scores")
        for score, outcomes in sensitivity["paired_scores"].items():
            q = f"{p}.paired_scores.{score}"
            _keys(outcomes, ("full_training", "standardized_training"), q)
            for outcome, association in outcomes.items():
                r = f"{q}.{outcome}"
                _keys(association, ("spearman", "perm_p"), r)
                _number(association["spearman"], f"{r}.spearman", -1, 1)
                _number(association["perm_p"], f"{r}.perm_p", 1.0 / 2001, 1)

    run = result["run"]
    p = f"{path}.run"
    _keys(
        run,
        (
            "grid",
            "split",
            "seed",
            "permutation_seed",
            "n_permutations",
            "permutation_probability",
            "permutation_probability_source",
            "rarefaction_method",
            "rarefaction_source",
            "rarefaction_reps",
            "rarefaction_seed",
            "K",
            "train_only_axes",
            "score_scope",
            "outcome",
            "limitations",
            "observation_rows",
            "observation_frame_sha256",
            "input_sha256",
            "source_sha256",
            "versions",
        ),
        p,
    )
    for name, expected in (
        ("split", "2025-07-01"),
        ("seed", 0),
        ("permutation_seed", 7),
        ("n_permutations", 2000),
        ("K", 5),
        ("rarefaction_method", "exact hypergeometric expectation"),
        ("rarefaction_reps", None),
        ("rarefaction_seed", None),
        ("train_only_axes", ["discover"]),
    ):
        _literal(run[name], expected, f"{p}.{name}")
    for name in ("permutation_probability", "permutation_probability_source", "rarefaction_source"):
        _string(run[name], f"{p}.{name}")
    _integer(run["observation_rows"], f"{p}.observation_rows", minimum=1)
    _hashes(
        {name: run[name] for name in ("input_sha256", "observation_frame_sha256")},
        ("input_sha256", "observation_frame_sha256"),
        p,
    )
    _hashes(run["source_sha256"], SOURCE_FILES, f"{p}.source_sha256")
    _versions(run["versions"], f"{p}.versions")
    _keys(run["score_scope"], SCORES, f"{p}.score_scope")
    for name, scope in run["score_scope"].items():
        _string(scope, f"{p}.score_scope.{name}")
    for name, keys in (
        ("outcome", ("unit", "baseline", "ranks", "definition", "legacy_field_names")),
        (
            "limitations",
            (
                "split_field",
                "historical_availability",
                "identifications",
                "inventory",
                "record_scope",
                "previously_unobserved_cells",
                "effort_unit",
                "exchangeability",
                "spatial_dependence",
                "multiple_comparisons",
                "inference",
            ),
        ),
    ):
        _keys(run[name], keys, f"{p}.{name}")
        for key, value in run[name].items():
            if key == "ranks":
                _literal(value, ["species", "subspecies", "variety", "form", "hybrid"], f"{p}.{name}.{key}")
            else:
                _string(value, f"{p}.{name}.{key}")
    grid = run["grid"]
    p = f"{p}.grid"
    _keys(
        grid,
        (
            "res_m",
            "crs",
            "lattice",
            "cell_keys",
            "n_inventory_cells",
            "n_eligible_cells",
            "inventory_sha256",
            "source_sha256",
        ),
        p,
    )
    _literal(grid["res_m"], 25000, f"{p}.res_m")
    _literal(grid["cell_keys"], ["row", "col"], f"{p}.cell_keys")
    _string(grid["crs"], f"{p}.crs")
    for name in ("n_inventory_cells", "n_eligible_cells"):
        _integer(grid[name], f"{p}.{name}", minimum=1)
    if grid["n_eligible_cells"] > grid["n_inventory_cells"]:
        _fail(f"{p}.n_eligible_cells", "exceeds n_inventory_cells")
    _keys(grid["lattice"], ("x0", "y1", "ncol", "nrow", "cell_km2"), f"{p}.lattice")
    for name in ("x0", "y1"):
        if type(grid["lattice"][name]) is not int:
            _fail(f"{p}.lattice.{name}", "expected integer")
    for name in ("ncol", "nrow"):
        _integer(grid["lattice"][name], f"{p}.lattice.{name}", minimum=1)
    _literal(grid["lattice"]["cell_km2"], 625.0, f"{p}.lattice.cell_km2")
    _hashes({"inventory": grid["inventory_sha256"]}, ("inventory",), f"{p}.inventory_sha256")
    _hashes(grid["source_sha256"], ("index", "group", "canada_mask"), f"{p}.source_sha256")


def compare_json(expected, actual, path="$", tolerance=1e-12):
    """Compare every JSON key and exact scalar type; tolerate only finite float rounding."""
    if type(expected) is not type(actual):
        _fail(path, f"type differs ({type(expected).__name__} versus {type(actual).__name__})")
    if type(expected) is dict:
        _keys(actual, expected.keys(), path)
        for key in expected:
            compare_json(expected[key], actual[key], f"{path}.{key}", tolerance)
    elif type(expected) is list:
        if len(expected) != len(actual):
            _fail(path, "array length differs")
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            compare_json(left, right, f"{path}[{index}]", tolerance)
    elif type(expected) is float:
        if not math.isfinite(expected) or not math.isfinite(actual):
            _fail(path, "non-finite JSON float")
        if not math.isclose(expected, actual, rel_tol=tolerance, abs_tol=tolerance):
            _fail(path, "float differs beyond 1e-12 tolerance")
    elif expected != actual:
        _fail(path, "value differs")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _read_json(path):
    try:
        return json.loads(
            path.read_text(),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: _fail(str(path), "non-finite JSON constant"),
        )
    except (OSError, ValueError) as error:
        raise VerificationError(f"{path}: cannot read valid JSON ({type(error).__name__})") from error


def load_expectations(root):
    cases, seen = [], set()
    for region, filename in RESULT_FILES.items():
        results = _read_json(root / "cluster_results" / filename)
        if type(results) is not list:
            _fail(filename, "expected JSON array of benchmark cases")
        for index, result in enumerate(results):
            path = f"{filename}[{index}]"
            validate_result(result, path)
            key = (result["region"], result["taxon"])
            if key[0] != region or key[1] not in CASES[region]:
                _fail(path, "unexpected taxon-region case")
            if key in seen:
                _fail(path, "duplicate taxon-region case")
            seen.add(key)
            cases.append(BenchmarkCase(*key, result))
    required = {(region, taxon) for region, taxa in CASES.items() for taxon in taxa}
    if seen != required:
        _fail("$", f"expected exactly eight cases; missing={sorted(required - seen)}")
    return cases


def file_sha256(path):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise VerificationError(f"{path}: cannot read input ({type(error).__name__})") from error
    return digest.hexdigest()


def _check_hash(path, expected, json_path):
    try:
        digest = file_sha256(path)
    except VerificationError as error:
        raise VerificationError(f"{json_path}: cannot read {path.name}") from error
    if digest != expected:
        _fail(json_path, f"SHA-256 mismatch for {path.name}; existing files are never overwritten")


def preflight_inventory(root, cases):
    """Validate every frozen source and inventory before any download or analysis."""
    data_dir = root / "cluster_results" / "ca"
    for case in cases:
        run = case.expected["run"]
        for name in SOURCE_FILES:
            _check_hash(root / name, run["source_sha256"][name], f"{case.path}.run.source_sha256.{name}")
        for name, filename in (("index", "index.json"), ("canada_mask", "us_cells.json")):
            _check_hash(
                data_dir / filename,
                run["grid"]["source_sha256"][name],
                f"{case.path}.run.grid.source_sha256.{name}",
            )
    index = _read_json(data_dir / "index.json")
    if type(index) is not dict or type(index.get("files")) is not dict:
        _fail("$.run.grid.source_sha256.index", "index must contain a group file map")
    inventories = {}
    for case in cases:
        filename = index.get("files", {}).get(case.taxon)
        if type(filename) is not str or Path(filename).name != filename:
            _fail(f"{case.path}.run.grid", "index must name a local group file")
        _check_hash(
            data_dir / filename,
            case.expected["run"]["grid"]["source_sha256"]["group"],
            f"{case.path}.run.grid.source_sha256.group",
        )
        if case.taxon not in inventories:
            try:
                inventories[case.taxon] = bas.AppGrid(case.taxon, data_dir).metadata
            except (ValueError, OSError, KeyError, TypeError) as error:
                raise VerificationError(
                    f"{case.path}.run.grid: invalid AppGrid inventory ({type(error).__name__})"
                ) from error
        compare_json(case.expected["run"]["grid"], inventories[case.taxon], f"{case.path}.run.grid")
    for name, actual, expected in (
        ("split", bas.vb.SPLIT.date().isoformat(), "2025-07-01"),
        ("seed", bas.vb.SEED, 0),
        ("n_permutations", bas.vb.N_PERM, 2000),
    ):
        _literal(actual, expected, f"$.runtime.{name}")
    return data_dir


def download_input(path, expected_hash):
    """Stream into a private temporary file, then atomically install without overwriting."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".part", delete=False
        ) as target:
            temporary = Path(target.name)
            digest = hashlib.sha256()
            with urllib.request.urlopen(f"{RELEASE}/{path.name}", timeout=60) as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            _fail(path.name, "download SHA-256 mismatch; no input installed")
        # link() installs atomically and refuses an existing destination, including a racing writer.
        os.link(temporary, path)
    except (OSError, urllib.error.URLError) as error:
        raise VerificationError(
            f"{path.name}: download could not be installed "
            f"({type(error).__name__}); existing files are never overwritten"
        ) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_inputs(cases, inputs_dir, download):
    paths, missing = {}, []
    for case in cases:
        path = inputs_dir / case.filename
        paths[case.filename] = path
        if path.exists():
            _check_hash(path, case.expected["run"]["input_sha256"], f"{case.path}.run.input_sha256")
        else:
            missing.append(case)
    if missing and not download:
        _fail(
            str(inputs_dir),
            f"missing {missing[0].filename}; supply archived assets with --inputs-dir "
            "or authorize downloads with --download",
        )
    if missing:
        inputs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for case in missing:
            download_input(paths[case.filename], case.expected["run"]["input_sha256"])
    # All eight input digests must pass before the first analysis.
    for case in cases:
        _check_hash(
            paths[case.filename], case.expected["run"]["input_sha256"], f"{case.path}.run.input_sha256"
        )
    return paths


def verify(root=ROOT, inputs_dir=None, download=False, report=print):
    if inputs_dir is not None and not inputs_dir.is_absolute():
        inputs_dir = root / inputs_dir
    cases = load_expectations(root)
    data_dir = preflight_inventory(root, cases)
    if inputs_dir is None and download:
        with tempfile.TemporaryDirectory(prefix="where-to-blitz-replay-") as temporary:
            return _replay(cases, data_dir, Path(temporary), download, report)
    return _replay(cases, data_dir, inputs_dir or root / "cluster_results", download, report)


def _replay(cases, data_dir, inputs_dir, download, report):
    paths = prepare_inputs(cases, inputs_dir, download)
    environments = set()
    for case in cases:
        path = paths[case.filename]
        try:
            result = bas.analyse(case.taxon, pd.read_csv(path), data_dir=data_dir, input_path=path)
            if result is None:
                _fail(case.path, "analysis returned insufficient data")
            actual = json.loads(json.dumps(result[0], allow_nan=False))
        except VerificationError:
            raise
        except Exception as error:
            # Parser and analysis messages can contain observation values; report only the exception type.
            raise VerificationError(f"{case.path}: analysis failed ({type(error).__name__})") from error
        actual["region"] = case.region
        validate_result(actual, case.path)
        expected = case.expected
        archived_versions, runtime_versions = expected["run"]["versions"], actual["run"]["versions"]
        # Versions are the only separately compared field. Every scientific and provenance field remains.
        left = dict(expected, run={key: value for key, value in expected["run"].items() if key != "versions"})
        right = dict(actual, run={key: value for key, value in actual["run"].items() if key != "versions"})
        compare_json(left, right, case.path)
        environments.add(
            (json.dumps(archived_versions, sort_keys=True), json.dumps(runtime_versions, sort_keys=True))
        )
        report(f"{case.region} {case.taxon}: complete scientific payload and provenance match")
    for archived, runtime in sorted(environments):
        report(f"Runtime versions, archived: {archived}")
        report(f"Runtime versions, replay:   {runtime}")
        report(
            "Runtime environment "
            + (
                "matches recorded versions"
                if archived == runtime
                else "differs; numerical agreement does not establish an identical environment"
            )
        )
    report(
        "Verified all 8 frozen retrospective sampled-record benchmarks on the committed 25 km inventory. "
        "This does not validate later rebuilt live Conservation data, the 5 km inventory or prospective trip yield."
    )
    return len(cases)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs-dir",
        type=Path,
        help="directory containing the eight archived inat CSV assets "
        "(relative paths are resolved from this script's directory)",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="download missing assets; use a temporary directory unless --inputs-dir is supplied",
    )
    args = parser.parse_args(argv)
    try:
        verify(inputs_dir=args.inputs_dir, download=args.download)
    except VerificationError as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
