"""Stage explicitly configured, validated optional layers for an atomic Pages build.

No configuration means no activation. Discovery artifacts are checksum pinned;
challenge snapshots must match the exact pinned approved combined species list.
All candidate files are validated before any last-good local file is replaced.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from build_discovery_layer import validate_artifact
from conservation_challenge import digest, query_filters, species_groups, validate_list, validate_snapshot

MAX_BYTES = 50_000_000


def validate_url(url):
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError("invalid optional layer URL")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or any(c.isspace() for c in url)
    ):
        raise ValueError("optional layers require HTTPS without URL credentials or fragments")


def download(url):
    validate_url(url)
    with requests.get(url, stream=True, timeout=(10, 60)) as response:
        response.raise_for_status()
        validate_url(response.url)
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError("optional layer exceeds the download limit")
        return bytes(data)


def pinned_json(url, checksum, fetch):
    validate_url(url)
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("configured layer checksum must be lowercase SHA256")
    data = fetch(url)
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError("configured layer checksum or size mismatch")
    return json.loads(data), data


def release_assets(repository):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid repository")

    def api(path, paginate=False):
        command = ["gh", "api", path]
        if paginate:
            command += ["--paginate", "--slurp"]
        return json.loads(subprocess.check_output(command, text=True, timeout=60))

    release = api(f"repos/{repository}/releases/tags/conservation-challenge-v1")
    pages = api(f"repos/{repository}/releases/{release['id']}/assets?per_page=100", paginate=True)
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ValueError("invalid release asset pagination")
    return [asset for page in pages for asset in page]


def prepare(
    directory,
    *,
    discovery_url="",
    discovery_sha="",
    taxa_url="",
    taxa_sha="",
    repository="PollockLab/where-to-blitz",
    fetch=download,
    assets=release_assets,
    now=None,
):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("preparation time must have a timezone")
    staged = {}
    if bool(discovery_url) != bool(discovery_sha) or bool(taxa_url) != bool(taxa_sha):
        raise ValueError("each configured input needs both its URL and checksum")
    if discovery_url:
        artifact, content = pinned_json(discovery_url, discovery_sha, fetch)
        validate_artifact(artifact)
        if (
            not isinstance(artifact["scope"]["filters"].get("taxon_group"), str)
            or not artifact["scope"]["filters"]["taxon_group"].strip()
        ):
            raise ValueError("discovery scope must name an explicit taxon_group for the viewer")
        staged["discovery_rate.json"] = content
    if taxa_url:
        config, _ = pinned_json(taxa_url, taxa_sha, fetch)
        validate_list(config)
        candidates = [
            a
            for a in assets(repository)
            if re.fullmatch(r"conservation-challenge-\d+-\d+\.json", a.get("name", ""))
            and a.get("state") == "uploaded"
        ]
        if not candidates:
            raise ValueError("no complete Conservation Challenge snapshot has been published")
        for asset in candidates:
            created = datetime.fromisoformat(asset["created_at"])
            if (
                created.tzinfo is None
                or created > now
                or type(asset.get("id")) is not int
                or asset["id"] <= 0
                or type(asset.get("size")) is not int
                or not 0 < asset["size"] <= MAX_BYTES
            ):
                raise ValueError("invalid challenge asset metadata")
        newest = max(candidates, key=lambda a: (datetime.fromisoformat(a["created_at"]), a["id"]))
        expected_url = (
            f"https://github.com/{repository}/releases/download/conservation-challenge-v1/{newest['name']}"
        )
        if newest.get("browser_download_url") != expected_url:
            raise ValueError("challenge asset URL does not belong to the configured release")
        validate_url(expected_url)
        content = fetch(newest["browser_download_url"])
        if len(content) > MAX_BYTES or len(content) != newest["size"]:
            raise ValueError("challenge snapshot size mismatch")
        if newest.get("digest") and newest["digest"] != "sha256:" + hashlib.sha256(content).hexdigest():
            raise ValueError("challenge asset checksum mismatch")
        snapshot = json.loads(content)
        validate_snapshot(snapshot)
        if snapshot["source"]["config_sha256"] != digest(config):
            raise ValueError("challenge snapshot belongs to a different combined species list")
        expected_source = {
            "url": config["source"]["url"],
            "sha256": config["source"]["sha256"],
            "version": config["version"],
            "config_sha256": digest(config),
        }
        if snapshot["source"] != expected_source:
            raise ValueError("challenge snapshot provenance differs from the approved list")
        as_of = datetime.fromisoformat(snapshot["as_of"])
        if as_of > now:
            raise ValueError("challenge snapshot is from the future")
        if snapshot["filters"] != query_filters(config, as_of):
            raise ValueError("challenge snapshot filters differ from the approved list configuration")
        groups = species_groups(config)
        for row in snapshot["observations"]:
            targets = row.get("target_ids", [row["taxon_id"]])
            if any(groups.get(target) != {"id": row["taxon_id"], "name": row["name"]} for target in targets):
                raise ValueError(
                    "challenge snapshot has observations outside the approved target/species mapping"
                )
        staged["conservation_challenge.json"] = content
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Validate and stage every candidate before replacing any local artifact. The
    # Pages deployment publishes the completed directory as a single site version.
    temporary_files = {}
    try:
        for name, content in staged.items():
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                temporary_files[name] = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        for name, temporary in temporary_files.items():
            temporary.replace(directory / name)
        for name in ("discovery_rate.json", "conservation_challenge.json"):
            if name not in staged:
                (directory / name).unlink(missing_ok=True)
    finally:
        for temporary in temporary_files.values():
            temporary.unlink(missing_ok=True)
    return {
        key: str((directory / name).resolve())
        for key, name in (
            ("DISCOVERY_RATE_FILE", "discovery_rate.json"),
            ("CONSERVATION_CHALLENGE_FILE", "conservation_challenge.json"),
        )
        if name in staged
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("cluster_results/ca"))
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    values = prepare(
        args.directory,
        discovery_url=os.environ.get("DISCOVERY_RATE_URL", ""),
        discovery_sha=os.environ.get("DISCOVERY_RATE_SHA256", ""),
        taxa_url=os.environ.get("CHALLENGE_TAXA_URL", ""),
        taxa_sha=os.environ.get("CHALLENGE_TAXA_SHA256", ""),
        repository=os.environ.get("GITHUB_REPOSITORY", "PollockLab/where-to-blitz"),
    )
    if args.env_file:
        with args.env_file.open("a") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    print("Prepared optional layers: " + ", ".join(key for key, value in values.items() if value))


if __name__ == "__main__":
    main()
