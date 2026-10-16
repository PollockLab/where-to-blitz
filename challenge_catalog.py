"""Expose only approved taxon metadata belonging to the exact deployed snapshot."""

import json
from datetime import datetime
from pathlib import Path

from conservation_challenge import (
    digest,
    query_filters,
    validate_list,
    validate_snapshot,
    validate_target_mapping,
)


def load_catalog(path, snapshot):
    """Unknown source vintages abstain rather than showing a different target list."""
    if snapshot is None or not Path(path).exists():
        return None
    config = json.loads(Path(path).read_text())
    validate_list(config)
    validate_snapshot(snapshot)
    expected = {
        "url": config["source"]["url"],
        "sha256": config["source"]["sha256"],
        "version": config["version"],
        "config_sha256": digest(config),
    }
    if (
        snapshot["source"] != expected
        or snapshot.get("coverage") != config.get("coverage")
        or snapshot["filters"] != query_filters(config, datetime.fromisoformat(snapshot["as_of"]))
    ):
        return None
    validate_target_mapping(snapshot, config)
    coverage = config.get("coverage")
    if coverage is not None:
        coverage = {
            key: coverage[key]
            for key in ("source_rows", "resolved_rows", "unique_targets", "omitted_rows", "omissions")
        }
    return {
        "source_config_sha256": expected["config_sha256"],
        "source_version": config["version"],
        "taxa": [{key: row[key] for key in ("id", "name", "rank")} for row in config["taxa"]],
        "coverage": coverage,
    }
