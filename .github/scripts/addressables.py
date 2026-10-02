"""Detect and validate Addressables deployments; uses only the standard library."""

import json
import os
from pathlib import Path
import subprocess
import sys


SUPPORTED_ENVIRONMENTS = ("development",)


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
    print(f"Validated {len(bundles)} bundles, {len(catalogs)} catalogs, {len(hashes)} hashes")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["detect"]:
            result = json.dumps(detect(), separators=(",", ":"))
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"envs={result}\n")
            print(f"Selected environments: {result}")
        elif sys.argv[1:] == ["validate"]:
            validate()
        else:
            raise ValueError("Usage: addressables.py detect|validate")
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
