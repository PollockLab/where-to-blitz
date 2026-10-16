"""Restore pinned aggregate count bands for a bounded Getting Even rebuild check."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

from gettingeven_inputs import _digest, _https, _read


def prepare(manifest, directory, *, source_sidecar, fetch=_read):
    doc = json.loads(Path(manifest).read_bytes())
    expected = {"grid_25000m/All_biodiversity.tif"} | {
        "grid_5000m/" + group + ".tif"
        for group in (
            "All_biodiversity",
            "Actinopterygii",
            "Fungi",
            "Amphibia",
            "Reptilia",
            "Arachnida",
            "Insecta",
            "Mollusca",
            "Mammalia",
            "Plantae",
        )
    }
    if (
        doc.get("schema") != "gettingeven-reference-counts@1"
        or set(doc.get("files", {})) != expected
        or not isinstance(doc.get("provenance"), dict)
        or not isinstance(doc["provenance"].get("observations_sha256"), dict)
        or set(doc["provenance"]["observations_sha256"]) != {"25000", "5000"}
    ):
        raise ValueError("reference manifest requires exact aggregate count bands and provenance")
    baseline = doc.get("published_outputs_sha256", {})
    if not isinstance(baseline, dict) or set(baseline) != {
        "webapp_data_gettingeven.json",
        "gettingeven_grid_25000m.png",
        "gettingeven_grid_5000m.png",
    }:
        raise ValueError("reference manifest requires the frozen published output baseline")
    for value in (
        doc["sha256"],
        *doc["files"].values(),
        *doc["provenance"]["observations_sha256"].values(),
        *baseline.values(),
    ):
        _digest(value)
    # Legacy public sidecars lack semantic input fingerprints. Bind the frozen
    # fixture to audited committed provenance saved before restoring that bundle.
    source_path = Path(source_sidecar)
    source = json.loads(source_path.read_bytes())
    for resolution, fingerprint in doc["provenance"]["observations_sha256"].items():
        tier = source.get("tiers", {}).get(resolution, {})
        if tier.get("inputs", {}).get("observations_sha256") != fingerprint:
            raise ValueError("reference observations differ from saved source provenance")
        png = f"gettingeven_grid_{resolution}m.png"
        if tier.get("png") != png or tier.get("sha256") != baseline[png]:
            raise ValueError("reference published baseline differs from saved source provenance")
        mask = tier.get("gate_mask", {})
        mask_name = f"gettingeven_gate_{resolution}m.png"
        if mask.get("png") != mask_name or hashlib.sha256(
            (source_path.parent / mask_name).read_bytes()
        ).hexdigest() != mask.get("sha256"):
            raise ValueError("saved source gate mask checksum mismatch")
    for name, checksum in baseline.items():
        if hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest() != checksum:
            raise ValueError("restored published outputs differ from the frozen reference baseline")
    _https(doc["source"])
    content = fetch(doc["source"])
    if hashlib.sha256(content).hexdigest() != doc.get("sha256"):
        raise ValueError("reference archive checksum mismatch")
    staged = {}
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) != len(expected) or {m.name for m in members} != expected:
            raise ValueError("reference archive must contain exactly the declared count bands")
        for member in members:
            if not member.isfile() or member.size > 10_000_000:
                raise ValueError("reference count bands must be bounded regular files")
            data = archive.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != doc["files"][member.name]:
                raise ValueError("reference count band checksum mismatch")
            staged[member.name] = data
    for name, data in staged.items():
        path = Path(directory) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="gettingeven_reference.json")
    parser.add_argument("--directory", default="cluster_results/ca")
    parser.add_argument(
        "--source-sidecar",
        required=True,
        help="audited sidecar saved with its gate masks before restoring the public bundle",
    )
    args = parser.parse_args()
    prepare(args.manifest, args.directory, source_sidecar=args.source_sidecar)
