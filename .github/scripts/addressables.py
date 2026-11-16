"""Detect and validate Addressables deployments; uses only the standard library."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys


SUPPORTED_ENVIRONMENTS = ("development",)

# The file the game reads first: which release this is and which catalogue it uses. Written by
# the addressable creator's "Build Content (Next Version)" menu, uploaded last.
VERSION_FILE = "version.json"
CATALOG_NAME = re.compile(r"^catalog[^/\\]*\.(bin|json)$")


def git(*args):
    return subprocess.check_output(["git", *args])


def detect():
    requested = os.environ.get("REQUESTED_ENV", "")
    if requested:
        if requested not in SUPPORTED_ENVIRONMENTS:
            raise ValueError(f"Unsupported environment: {requested}")
        if not Path(requested).is_dir():
            raise ValueError(f"Environment folder missing: {requested}")
        return [requested]

    before = os.environ.get("BEFORE_SHA", "")
    after = os.environ["AFTER_SHA"]
    # A force-push can make the old commit unavailable in a fresh checkout.
    available = before and before != "0" * 40 and subprocess.run(
        ["git", "cat-file", "-e", f"{before}^{{commit}}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if available:
        paths = git("diff", "--name-only", "--no-renames", "-z", before, after)
    else:
        paths = git("ls-tree", "-r", "--name-only", "-z", after)
    changed = {p.split(b"/", 1)[0] for p in paths.split(b"\0") if b"/" in p}
    return [name for name in SUPPORTED_ENVIRONMENTS
            if name.encode() in changed and Path(name).is_dir()]


def validate():
    name = os.environ.get("ENV_NAME", "")
    if name not in SUPPORTED_ENVIRONMENTS:
        raise ValueError(f"Unsupported environment: {name}")
    for key in ("S3_BUCKET", "AWS_REGION", "AWS_ROLE_ARN", "S3_PREFIX"):
        if not os.environ.get(key, "").strip():
            raise ValueError(f"Missing GitHub configuration variable: {key}")
    bucket = os.environ["S3_BUCKET"]
    if "/" in bucket or ":" in bucket or bucket != bucket.strip():
        raise ValueError("S3_BUCKET must be a bucket name, without s3:// or a path")
    prefix = os.environ["S3_PREFIX"]
    if "\\" in prefix or any(p in ("", ".", "..") for p in prefix.split("/")):
        raise ValueError("S3_PREFIX must have no empty, dot, or backslash path segments")

    root = Path(name)
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Environment must be a real directory: {name}")
    paths = list(root.rglob("*"))
    if any(p.is_symlink() for p in paths):
        raise ValueError("Addressables output must not contain symbolic links")
    files = [p for p in paths if p.is_file()]
    bundles = [p for p in files if p.suffix == ".bundle"]
    catalogs = [p for p in files if p.name.startswith("catalog")
                and p.suffix in (".bin", ".json")]
    hashes = [p for p in files if p.name.startswith("catalog") and p.suffix == ".hash"]
    if not bundles or not catalogs or not hashes:
        raise ValueError("Output requires .bundle, catalog*.bin/json, and catalog*.hash files")
    for path in bundles + catalogs + hashes:
        if path.stat().st_size == 0:
            raise ValueError(f"Empty output file: {path}")
    for catalog in catalogs:
        if catalog.with_suffix(".hash") not in hashes:
            raise ValueError(f"Missing matching hash for {catalog}")
    for hash_file in hashes:
        if not any(hash_file.with_suffix(ext) in catalogs for ext in (".bin", ".json")):
            raise ValueError(f"Missing matching catalog for {hash_file}")
    version, catalog = read_version(root / VERSION_FILE)
    if not (root / catalog).is_file():
        raise ValueError(f"{VERSION_FILE} names {catalog}, which is not in {name}/")
    if not (root / catalog).with_suffix(".hash").is_file():
        raise ValueError(f"{VERSION_FILE} names {catalog}, which has no matching hash in {name}/")
    print(f"Validated {len(bundles)} bundles, {len(catalogs)} catalogs, {len(hashes)} hashes, "
          f"content version {version} ({catalog})")


def read_version(path):
    """The release number and catalogue named by a version.json; refuses anything else."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Missing {path}: build with Tools > Dojo > Addressables > "
                         "3. Build Content (Next Version)")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"{path} is not valid JSON: {error}")
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a JSON object")
    version = data.get("version")
    if type(version) is not int or version < 0:
        raise ValueError(f"{path} needs \"version\": a whole number of 0 or more")
    catalog = data.get("catalog")
    if not isinstance(catalog, str) or ".." in catalog or not CATALOG_NAME.match(catalog):
        raise ValueError(f"{path} needs \"catalog\": a catalog*.bin or catalog*.json file name")
    return version, catalog


def check_version():
    """The new version must follow the published one: exactly one higher, or the same release
    again unchanged (a re-run). With nothing published yet, it must be 0."""
    name = os.environ.get("ENV_NAME", "")
    if name not in SUPPORTED_ENVIRONMENTS:
        raise ValueError(f"Unsupported environment: {name}")
    root = Path(name)
    version, catalog = read_version(root / VERSION_FILE)

    published_file = Path(os.environ.get("PUBLISHED_VERSION_FILE", ""))
    if not os.environ.get("PUBLISHED_VERSION_FILE") or not published_file.is_file():
        if version != 0:
            raise ValueError(f"Nothing is published yet, so the first version must be 0, not {version}")
        print(f"First published content version: 0 ({catalog})")
        return

    published, published_catalog = read_version(published_file)
    if version == published + 1:
        print(f"Content version {published} -> {version} ({catalog})")
        return
    if version == published:
        local_hash = (root / catalog).with_suffix(".hash").read_text(encoding="utf-8-sig").strip()
        hash_file = os.environ.get("PUBLISHED_HASH_FILE", "")
        published_hash = (Path(hash_file).read_text(encoding="utf-8-sig").strip()
                          if hash_file and Path(hash_file).is_file() else None)
        if catalog == published_catalog and published_hash is not None and local_hash == published_hash:
            print(f"Content version {version} is already published and unchanged: republishing it")
            return
        raise ValueError(f"Content version {version} is already published with different content. "
                         f"Build the next version ({published + 1}) instead")
    if version < published:
        raise ValueError(f"Content version {version} is lower than the published version {published}. "
                         f"To go back, publish the old content again as version {published + 1}")
    raise ValueError(f"Content version {version} skips ahead: the published version is {published}, "
                     f"so the next must be {published + 1}")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["detect"]:
            result = json.dumps(detect(), separators=(",", ":"))
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"envs={result}\n")
            print(f"Selected environments: {result}")
        elif sys.argv[1:] == ["validate"]:
            validate()
        elif sys.argv[1:] == ["check-version"]:
            check_version()
        else:
            raise ValueError("Usage: addressables.py detect|validate|check-version")
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
