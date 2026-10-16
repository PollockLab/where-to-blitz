"""Stage pinned source-count evidence only for its matching published map outputs."""

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from prepare_optional_layers import download, validate_url

DEFAULT_SOURCE = Path(__file__).with_name("gettingeven_evidence_source.json")
TIERS = ("25000", "5000")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _reference(value):
    if not isinstance(value, dict) or set(value) != {"url", "sha256"}:
        raise ValueError("count evidence source requires URL and SHA256")
    validate_url(value["url"])
    if not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        raise ValueError("count evidence source requires a lowercase SHA256")


def _configuration(source):
    config = json.loads(source.read_bytes())
    if (
        not isinstance(config, dict)
        or config.get("schema_version") != 1
        or not isinstance(config.get("counts"), dict)
        or set(config["counts"]) != set(TIERS)
        or not isinstance(config.get("published_outputs_sha256"), dict)
        or set(config["published_outputs_sha256"])
        != {"webapp_data_gettingeven.json", *(f"gettingeven_grid_{res}m.png" for res in TIERS)}
    ):
        raise ValueError("invalid count evidence source configuration")
    for reference in (config.get("manifest"), *config["counts"].values()):
        _reference(reference)
    for expected in config["published_outputs_sha256"].values():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("invalid count evidence map fingerprint")
    return config


def _matching_outputs(directory, config):
    for name, expected in config["published_outputs_sha256"].items():
        path = directory / name
        if not path.exists() or _sha(path.read_bytes()) != expected:
            return False
    return True


def load_installed(directory, source=DEFAULT_SOURCE):
    """Require an authentic publisher binding as well as internally consistent images."""
    from gettingeven_evidence import validate_public_evidence

    directory, source = Path(directory), Path(source)
    installed = directory / "gettingeven_evidence.json"
    if not installed.exists():
        return None
    evidence = validate_public_evidence(directory, directory)
    sidecar = json.loads((directory / "gettingeven_grid.json").read_bytes())
    native = all(
        sidecar["tiers"][res].get("evidence")
        == {
            "png": evidence["tiers"][res]["png"],
            "sha256": evidence["tiers"][res]["sha256"],
            "observations_sha256": evidence["tiers"][res]["observations_sha256"],
        }
        and sidecar["tiers"][res].get("inputs", {}).get("observations_sha256")
        == evidence["tiers"][res]["observations_sha256"]
        for res in TIERS
    )
    if native:
        return evidence
    if not source.exists():
        raise ValueError("legacy count evidence requires its pinned producer manifest")
    config = _configuration(source)
    if (
        evidence["manifest_sha256"] != config["manifest"]["sha256"]
        or not _matching_outputs(directory, config)
        or any(evidence["tiers"][res]["sha256"] != config["counts"][res]["sha256"] for res in TIERS)
    ):
        raise ValueError("installed count evidence differs from its pinned producer")
    return evidence


def prepare(directory, source=DEFAULT_SOURCE, *, fetch=download):
    """Unknown map versions abstain; malformed matching evidence fails before install."""
    from gettingeven_evidence import validate_public_evidence

    directory, source = Path(directory), Path(source)
    if (directory / "gettingeven_evidence.json").exists():
        return load_installed(directory, source)
    if not source.exists():
        return None
    config = _configuration(source)
    if not _matching_outputs(directory, config):
        return None
    payload = fetch(config["manifest"]["url"])
    if len(payload) > 1_000_000 or _sha(payload) != config["manifest"]["sha256"]:
        raise ValueError("count evidence manifest checksum or size mismatch")
    manifest = json.loads(payload)
    if not isinstance(manifest, dict) or set(manifest.get("tiers", {})) != set(TIERS):
        raise ValueError("count evidence manifest requires both tiers")
    files = {"gettingeven_evidence.json": payload}
    for res in TIERS:
        tier = manifest["tiers"][res]
        name = f"gettingeven_counts_{res}m.png"
        if not isinstance(tier, dict) or tier.get("png") != name:
            raise ValueError("count evidence requires exact tier filenames")
        reference = config["counts"][res]
        if tier.get("sha256") != reference["sha256"]:
            raise ValueError("count evidence manifest and image pins disagree")
        content = fetch(reference["url"])
        if len(content) > 10_000_000 or _sha(content) != reference["sha256"]:
            raise ValueError("count evidence image checksum or size mismatch")
        files[name] = content
    # The manifest becomes visible last, after all paired images validate.
    with tempfile.TemporaryDirectory(dir=directory, prefix=".ge-evidence-") as temporary:
        staged = Path(temporary)
        for name, content in files.items():
            (staged / name).write_bytes(content)
        result = validate_public_evidence(directory, staged)
        for name in [*(f"gettingeven_counts_{res}m.png" for res in TIERS), "gettingeven_evidence.json"]:
            os.replace(staged / name, directory / name)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("cluster_results/ca"))
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    result = prepare(args.directory, args.source)
    print(
        json.dumps({"getting_even_count_evidence": "ready" if result else "unavailable for this map version"})
    )
