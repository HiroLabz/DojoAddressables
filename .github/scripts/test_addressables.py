"""Run: python -m unittest discover -s .github/scripts -v"""
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

    def assets(self, folder="development", extension="bin"):
        self.write(f"{folder}/asset.bundle")
        self.write(f"{folder}/catalog_1.{extension}")
        self.write(f"{folder}/catalog_1.hash")

    def test_flat_and_nested_output(self):
        self.assets()
        self.assets("development/Android")
        self.assets("development/WebGL", "json")
        addressables.validate()

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


if __name__ == "__main__":
    unittest.main()
