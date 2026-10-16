"""Pinned private or hosted richness inputs and semantic observation fingerprints.

GE_RICHNESS_MANIFEST points to a local JSON file or HTTPS URL. Hosted manifests also
require GE_RICHNESS_MANIFEST_SHA256. Schema: {"schema": "gettingeven-richness@1",
"sources": {"Plants": {"source": "plant.tif", "sha256": "...", "provenance": "..."},
"Mammals": {...}, "Reptiles & Amphibians": {...}}}. Local relative sources resolve
beside the manifest. HTTPS sources are cached by their expected checksum. No source
location, credentials or redistribution permission is inferred from these records.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen

import numpy as np

from build_provenance import sha256_file

RICHNESS_ENV = {
    "Reptiles & Amphibians": "GE_RICHNESS_HERP",
    "Mammals": "GE_RICHNESS_MAMMAL",
    "Plants": "GE_RICHNESS_PLANT",
}


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("richness sha256 must be 64 lowercase hexadecimal characters")
    return value


def _https(source):
    url = urlsplit(source)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("hosted richness inputs require HTTPS without credentials or fragments")


def _read(source):
    if urlsplit(source).scheme:
        _https(source)
        with urlopen(source, timeout=120) as response:
            _https(response.url)
            return response.read()
    return Path(source).read_bytes()


def load_richness_inputs():
    """Return category paths and public checksum records, never private source locations."""
    manifest = os.environ.get("GE_RICHNESS_MANIFEST")
    if not manifest:
        paths = {cat: os.environ[env] for cat, env in RICHNESS_ENV.items() if os.environ.get(env)}
        if paths and set(paths) != set(RICHNESS_ENV):
            raise ValueError("supply all three GE_RICHNESS_* rasters, or none")
        return paths, {
            cat: {"sha256": sha256_file(path), "provenance": "local raster"} for cat, path in paths.items()
        }
    if any(os.environ.get(env) for env in RICHNESS_ENV.values()):
        raise ValueError("use GE_RICHNESS_MANIFEST or GE_RICHNESS_* paths, not both")
    expected = os.environ.get("GE_RICHNESS_MANIFEST_SHA256")
    hosted = bool(urlsplit(manifest).scheme)
    if hosted and not expected:
        raise ValueError("hosted manifest requires GE_RICHNESS_MANIFEST_SHA256")
    content = _read(manifest)
    if expected and hashlib.sha256(content).hexdigest() != _digest(expected):
        raise ValueError("richness manifest checksum mismatch")
    doc = json.loads(content)
    if doc.get("schema") != "gettingeven-richness@1" or set(doc.get("sources", {})) != set(RICHNESS_ENV):
        raise ValueError(
            "manifest must declare gettingeven-richness@1 and exactly the three richness categories"
        )
    paths, records = {}, {}
    cache = Path(os.environ.get("GE_RICHNESS_CACHE", tempfile.gettempdir())) / "gettingeven-richness"
    for cat, item in doc["sources"].items():
        digest = _digest(item.get("sha256"))
        source, provenance = item.get("source"), item.get("provenance")
        if (
            not isinstance(source, str)
            or not source
            or not isinstance(provenance, str)
            or not provenance.strip()
        ):
            raise ValueError(f"{cat}: nonempty source and provenance are required")
        source = urljoin(manifest, source) if hosted else source
        if not urlsplit(source).scheme:
            source = str(Path(manifest).resolve().parent / source)
        if hosted or urlsplit(source).scheme:
            _https(source)
            cache.mkdir(parents=True, exist_ok=True)
            path = cache / f"{digest}.tif"
            if not path.exists() or sha256_file(path) != digest:
                data = _read(source)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f"{cat}: richness checksum mismatch")
                with tempfile.NamedTemporaryFile(dir=cache, delete=False) as fh:
                    fh.write(data)
                    temporary = Path(fh.name)
                temporary.replace(path)
        else:
            path = Path(source)
        if sha256_file(path) != digest:
            raise ValueError(f"{cat}: richness checksum mismatch")
        paths[cat] = str(path)
        records[cat] = {"sha256": digest, "provenance": provenance}
    return paths, records


def observation_hash(rowcol, total, counts):
    """Bind actual selected counts, cell ordering and grouping, independent of TIFF metadata."""
    digest = hashlib.sha256(b"gettingeven-observations@1\0")
    for values in (*rowcol, total, counts):
        data = np.asarray(values, dtype="<f8").copy()
        data[np.isnan(data)] = np.nan
        digest.update(json.dumps(data.shape).encode() + b"\0" + data.tobytes())
    return digest.hexdigest()
