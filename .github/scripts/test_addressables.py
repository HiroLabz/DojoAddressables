"""Run: python -m unittest discover -s .github/scripts -v"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import addressables


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        previous = Path.cwd()
        temp = tempfile.TemporaryDirectory(dir=previous)
        self.addCleanup(temp.cleanup)
        self.addCleanup(os.chdir, previous)
        os.chdir(temp.name)
        self.enterContext(patch.dict(os.environ, {
            "REQUESTED_ENV": "", "ENV_NAME": "development", "S3_BUCKET": "test-assets",
            "AWS_REGION": "ap-southeast-1", "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/test",
            "S3_PREFIX": "addressables",
        }))

    def write(self, path, content="asset"):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", *args],
            stderr=subprocess.DEVNULL, timeout=20,
        ).decode().strip()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def detect(self, before, after):
        with patch.dict(os.environ, {"BEFORE_SHA": before, "AFTER_SHA": after}):
            return addressables.detect()

    def test_push_scenarios(self):
        self.git("init")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.write("development/file.bundle")
        initial = self.commit()
        self.assertEqual(self.detect("0" * 40, initial), ["development"])
        self.assertEqual(self.detect("f" * 40, initial), ["development"])
        self.assertEqual(self.detect(initial, initial), [])
        self.write("README.md")
        docs = self.commit()
        self.assertEqual(self.detect(initial, docs), [])
        for path in (".github/workflows/deploy.yml", ".vscode/settings.json", "unknown/file.bundle"):
            self.write(path)
        tooling = self.commit()
        self.assertEqual(self.detect(docs, tooling), [])
        self.write("development/Android/file with spaces.bundle")
        assets = self.commit()
        self.assertEqual(self.detect(tooling, assets), ["development"])
        self.git("mv", "development/Android/file with spaces.bundle", "unknown/moved.bundle")
        renamed = self.commit()
        self.assertEqual(self.detect(assets, renamed), ["development"])
        self.git("rm", "development/file.bundle")
        if Path("development/Android").exists():
            Path("development/Android").rmdir()
        if Path("development").exists():
            Path("development").rmdir()
        removed = self.commit()
        self.assertEqual(self.detect(renamed, removed), [])

    def test_manual_selection(self):
        Path("development").mkdir()
        with patch.dict(os.environ, {"REQUESTED_ENV": "development"}):
            self.assertEqual(addressables.detect(), ["development"])
        for name in (".github", "../development", "production"):
            with patch.dict(os.environ, {"REQUESTED_ENV": name}):
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    addressables.detect()
        Path("development").rmdir()
        with patch.dict(os.environ, {"REQUESTED_ENV": "development"}):
            with self.assertRaisesRegex(ValueError, "missing"):
                addressables.detect()

    def assets(self, folder="development", extension="bin", version=1):
        self.write(f"{folder}/asset.bundle")
        self.write(f"{folder}/catalog_{version}.{extension}")
        self.write(f"{folder}/catalog_{version}.hash", f"hash{version}")
        if folder == "development":
            self.version(version)

    def version(self, number, catalog=None, path="development/version.json"):
        catalog = catalog or f"catalog_{number}.bin"
        self.write(path, json.dumps({"version": number, "catalog": catalog}))

    def test_flat_and_nested_output(self):
        self.assets()
        self.assets("development/Android")
        self.assets("development/WebGL", "json")
        addressables.validate()

    def test_missing_version_file(self):
        self.assets()
        Path("development/version.json").unlink()
        with self.assertRaisesRegex(ValueError, "Missing .*version.json"):
            addressables.validate()

    def test_version_names_a_missing_catalog(self):
        self.assets()
        self.version(1, catalog="catalog_9.bin")
        with self.assertRaisesRegex(ValueError, "catalog_9.bin, which is not in development/"):
            addressables.validate()

    def test_version_folders_of_the_old_layout_are_refused(self):
        self.assets()
        self.write("development/v1/asset.bundle")
        self.write("development/v1/catalog_1.bin")
        self.write("development/v1/catalog_1.hash")
        with self.assertRaisesRegex(ValueError, r"version folders from the old layout \(v1\)"):
            addressables.validate()

    def test_one_release_only(self):
        self.assets(version=2)
        self.write("development/catalog_1.bin")
        self.write("development/catalog_1.hash")
        with self.assertRaisesRegex(ValueError, "does not name: catalog_1.bin"):
            addressables.validate()

    def test_release_needs_bundles_beside_version_json(self):
        self.assets()
        self.assets("development/Android")
        Path("development/asset.bundle").unlink()
        with self.assertRaisesRegex(ValueError, "development/ has no bundles"):
            addressables.validate()

    def test_bad_version_files(self):
        self.assets()
        for content in ("not json", "[1]", '{"catalog": "catalog_1.bin"}',
                        '{"version": -1, "catalog": "catalog_1.bin"}',
                        '{"version": 1.5, "catalog": "catalog_1.bin"}',
                        '{"version": "1", "catalog": "catalog_1.bin"}',
                        '{"version": true, "catalog": "catalog_1.bin"}',
                        '{"version": 1}', '{"version": 1, "catalog": "../catalog_1.bin"}',
                        '{"version": 1, "catalog": "sub/catalog_1.bin"}',
                        '{"version": 1, "catalog": "catalog_1.hash"}'):
            with self.subTest(content=content):
                self.write("development/version.json", content)
                with self.assertRaises(ValueError):
                    addressables.validate()

    def check(self, published=None, published_hash=None):
        env = {"PUBLISHED_VERSION_FILE": "", "PUBLISHED_HASH_FILE": ""}
        if published is not None:
            self.version(published, path="published/version.json")
            env["PUBLISHED_VERSION_FILE"] = "published/version.json"
        if published_hash is not None:
            self.write("published/catalog.hash", published_hash)
            env["PUBLISHED_HASH_FILE"] = "published/catalog.hash"
        with patch.dict(os.environ, env):
            addressables.check_version()

    def test_first_version_must_be_zero(self):
        self.assets(version=0)
        self.check()
        self.assets(version=1)
        with self.assertRaisesRegex(ValueError, "must be 0"):
            self.check()

    def test_next_version_is_higher(self):
        self.assets(version=4)
        self.check(published=3)
        with self.assertRaisesRegex(ValueError, "lower"):
            self.check(published=5)

    def test_next_version_may_skip_one_that_never_reached_the_cdn(self):
        # v3 pushed but its deploy failed, so the CDN still has v2 when the Builder makes v4.
        self.assets(version=4)
        self.check(published=2)
        self.check(published=0)

    def test_same_version_only_when_unchanged(self):
        self.assets(version=3)
        self.check(published=3, published_hash="hash3")
        with self.assertRaisesRegex(ValueError, "different content"):
            self.check(published=3, published_hash="changed")
        with self.assertRaisesRegex(ValueError, "different content"):
            self.check(published=3)

    def test_missing_matching_hash(self):
        self.assets()
        self.write("development/catalog_2.bin")
        with self.assertRaisesRegex(ValueError, "matching hash"):
            addressables.validate()

    def test_orphan_hash(self):
        self.assets()
        self.write("development/catalog_2.hash")
        with self.assertRaisesRegex(ValueError, "matching catalog"):
            addressables.validate()

    def test_empty_bundle(self):
        self.assets()
        self.write("development/asset.bundle", "")
        with self.assertRaisesRegex(ValueError, "Empty output"):
            addressables.validate()

    def test_missing_bundles(self):
        self.assets()
        Path("development/asset.bundle").unlink()
        with self.assertRaisesRegex(ValueError, "Output requires"):
            addressables.validate()

    def test_invalid_configuration(self):
        self.assets()
        for key, value in (("AWS_REGION", ""), ("S3_BUCKET", "s3://bucket"),
                           ("S3_PREFIX", "/addressables/"), ("S3_PREFIX", "a/../b")):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                with self.assertRaises(ValueError):
                    addressables.validate()


    def cdn(self, served):
        def fetch(url):
            name = url.rsplit("/", 1)[1]
            return served.get(name, (403, b""))
        with patch.dict(os.environ, {"CONTENT_URL": "https://cdn.example/addressables/"}):
            addressables.check_cdn(fetch)

    def test_cdn_new_names_and_unchanged_bundles_pass(self):
        self.assets()
        self.write("development/new.bundle", "fresh")
        self.cdn({"asset.bundle": (200, b"asset")})

    def test_cdn_same_name_with_different_bytes_is_refused(self):
        self.assets()
        with self.assertRaisesRegex(ValueError, "CRC check: asset.bundle"):
            self.cdn({"asset.bundle": (200, b"published long ago")})

    def test_cdn_errors_stop_the_publish(self):
        self.assets()
        with self.assertRaisesRegex(ValueError, "HTTP 500"):
            self.cdn({"asset.bundle": (500, b"")})


if __name__ == "__main__":
    unittest.main()
