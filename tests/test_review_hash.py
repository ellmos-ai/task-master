# -*- coding: utf-8 -*-
import os
import tempfile
import unicodedata
import unittest
from pathlib import Path

from taskplan.review_pool import ProjectHashError, hash_project


class TestProjectHash(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "projekt"
        self.project.mkdir()
        (self.project / "src").mkdir()
        (self.project / "src" / "main.py").write_text(
            "print('eins')\n", encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_tree_has_same_versioned_hash(self):
        first = hash_project(self.project)
        second = hash_project(self.project)
        self.assertEqual(first.value, second.value)
        self.assertTrue(first.value.startswith("sha256-v1:"))
        self.assertEqual(first.file_count, 1)

    def test_content_and_path_changes_break_hash_but_mtime_does_not(self):
        target = self.project / "src" / "main.py"
        original = hash_project(self.project).value

        stat = target.stat()
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        self.assertEqual(hash_project(self.project).value, original)

        target.write_text("print('zwei')\n", encoding="utf-8")
        changed = hash_project(self.project).value
        self.assertNotEqual(changed, original)

        target.rename(self.project / "src" / "renamed.py")
        self.assertNotEqual(hash_project(self.project).value, changed)

    def test_default_noise_and_custom_excludes_do_not_break_hash(self):
        baseline = hash_project(self.project, exclude=("private/**",)).value

        for directory in (".git", "build", "__pycache__", ".taskplan"):
            path = self.project / directory
            path.mkdir(exist_ok=True)
            (path / "noise.bin").write_bytes(b"noise")
        (self.project / "LOCK.execution-contract.txt").write_text(
            "owner=x", encoding="utf-8"
        )
        private = self.project / "private"
        private.mkdir()
        (private / "secret.txt").write_text("ignore", encoding="utf-8")

        self.assertEqual(
            hash_project(self.project, exclude=("private/**",)).value,
            baseline,
        )

        (self.project / "relevant.txt").write_text("include", encoding="utf-8")
        self.assertNotEqual(
            hash_project(self.project, exclude=("private/**",)).value,
            baseline,
        )

    def test_symlink_is_hashed_as_link_and_not_followed(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("eins", encoding="utf-8")
        link = self.project / "external-link"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"Symlinks sind auf diesem Host nicht verfügbar: {exc}")

        first = hash_project(self.project).value
        outside.write_text("zwei", encoding="utf-8")
        self.assertEqual(hash_project(self.project).value, first)

        link.unlink()
        link.symlink_to(Path(self.tmp.name) / "anderes-ziel.txt")
        self.assertNotEqual(hash_project(self.project).value, first)

    def test_missing_project_fails_closed(self):
        with self.assertRaises(ProjectHashError):
            hash_project(self.project / "fehlt")

    def test_nfc_ambiguous_paths_fail_closed(self):
        composed = "é.txt"
        decomposed = unicodedata.normalize("NFD", composed)
        if composed == decomposed:
            self.skipTest("Unicode-Normalisierung erzeugt keine zwei Namen")
        first = self.project / composed
        second = self.project / decomposed
        first.write_text("eins", encoding="utf-8")
        try:
            second.write_text("zwei", encoding="utf-8")
        except OSError as exc:
            self.skipTest(f"Dateisystem erlaubt keine NFC-Mehrdeutigkeit: {exc}")
        if len({entry.name for entry in self.project.iterdir()}) < 3:
            self.skipTest("Dateisystem vereinheitlicht Unicode-Dateinamen")

        with self.assertRaisesRegex(ProjectHashError, "Mehrdeutiger Projektpfad"):
            hash_project(self.project)


if __name__ == "__main__":
    unittest.main()
