#!/usr/bin/env python3
"""Tests for catalog-select.py.

The rule under test is the one that prevents a silent total loss of
detection: bumblebee refuses a catalog directory that mixes schema
versions and exits 2 before scanning, so the staging step MUST hand it a
single coherent group. These cases pin that behaviour.

Run: python3 -m unittest test_catalog_select   (from src/endpoint/)
     (or: python3 -m unittest discover -s deploy -p 'test_*.py')
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "catalog-select.py")


def write_catalog(path: str, schema_version, entries: int) -> str:
    doc = {"entries": [{"id": f"MAL-{i}", "ecosystem": "npm", "name": f"p{i}", "versions": ["1.0.0"]}
                       for i in range(entries)]}
    if schema_version is not None:
        doc["schema_version"] = schema_version
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return path


def run_select(out_dir: str, candidates: list[str], rank: int = 0):
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--out", out_dir, "--rank", str(rank), *candidates],
        capture_output=True, text=True,
    )
    summary = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, summary


class TestCatalogSelect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_single_schema_stages_everything(self):
        a = write_catalog(os.path.join(self.tmp, "curated.json"), "0.1.0", 5)
        b = write_catalog(os.path.join(self.tmp, "osv.json"), "0.1.0", 30)
        out = os.path.join(self.tmp, "stage")
        rc, s = run_select(out, [a, b])
        self.assertEqual(rc, 0)
        self.assertEqual(s["staged"], 2)
        self.assertEqual(s["entries"], 35)
        self.assertEqual(s["schema_version"], "0.1.0")
        self.assertFalse(s["schema_conflict"])
        self.assertEqual(sorted(os.listdir(out)), ["curated.json", "osv.json"])

    def test_mixed_schemas_never_staged_together(self):
        """The whole point: a mixed directory would make bumblebee exit 2."""
        curated = write_catalog(os.path.join(self.tmp, "curated.json"), "0.2.0", 11)
        osv = write_catalog(os.path.join(self.tmp, "osv.json"), "0.1.0", 30511)
        out = os.path.join(self.tmp, "stage")
        rc, s = run_select(out, [curated, osv])
        self.assertEqual(rc, 0)
        self.assertTrue(s["schema_conflict"], "conflict must be reported so an alert can fire")
        self.assertEqual(s["ranks"], 2)
        # Largest corpus wins: dropping 30511 entries to honour a version
        # bump on 11 curated files would gut detection.
        self.assertEqual(s["schema_version"], "0.1.0")
        self.assertEqual(s["staged"], 1)
        self.assertEqual(os.listdir(out), ["osv.json"])

    def test_rank_1_returns_the_other_group(self):
        """Caller falls back to rank 1 when the binary rejects rank 0."""
        curated = write_catalog(os.path.join(self.tmp, "curated.json"), "0.2.0", 11)
        osv = write_catalog(os.path.join(self.tmp, "osv.json"), "0.1.0", 30511)
        out = os.path.join(self.tmp, "stage")
        rc, s = run_select(out, [curated, osv], rank=1)
        self.assertEqual(rc, 0)
        self.assertEqual(s["schema_version"], "0.2.0")
        self.assertEqual(os.listdir(out), ["curated.json"])

    def test_rank_beyond_available_fails(self):
        a = write_catalog(os.path.join(self.tmp, "a.json"), "0.1.0", 3)
        out = os.path.join(self.tmp, "stage")
        rc, s = run_select(out, [a], rank=1)
        self.assertEqual(rc, 1)
        self.assertEqual(s["staged"], 0)

    def test_unusable_files_are_rejected_not_staged(self):
        good = write_catalog(os.path.join(self.tmp, "good.json"), "0.1.0", 4)
        empty = write_catalog(os.path.join(self.tmp, "empty.json"), "0.1.0", 0)
        noversion = write_catalog(os.path.join(self.tmp, "nover.json"), None, 4)
        unsupported = write_catalog(os.path.join(self.tmp, "future.json"), "9.9.9", 4)
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        out = os.path.join(self.tmp, "stage")
        rc, s = run_select(out, [good, empty, noversion, unsupported, broken])
        self.assertEqual(rc, 0)
        self.assertEqual(s["staged"], 1)
        self.assertEqual(s["rejected"], 4)
        self.assertEqual(os.listdir(out), ["good.json"])

    def test_no_usable_catalogs_signals_failure(self):
        """rc=1 is what tells the caller detection is UNAVAILABLE, not clean."""
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("nope")
        out = os.path.join(self.tmp, "stage")
        rc, s = run_select(out, [broken])
        self.assertEqual(rc, 1)
        self.assertEqual(s["staged"], 0)
        self.assertFalse(os.path.exists(out), "must not leave a half-built staging dir")

    def test_basename_collision_keeps_both(self):
        d1 = os.path.join(self.tmp, "a"); os.makedirs(d1)
        d2 = os.path.join(self.tmp, "b"); os.makedirs(d2)
        f1 = write_catalog(os.path.join(d1, "same.json"), "0.1.0", 2)
        f2 = write_catalog(os.path.join(d2, "same.json"), "0.1.0", 3)
        out = os.path.join(self.tmp, "stage")
        rc, s = run_select(out, [f1, f2])
        self.assertEqual(rc, 0)
        self.assertEqual(s["staged"], 2, "a name collision must not silently drop a catalog")
        self.assertEqual(len(os.listdir(out)), 2)


if __name__ == "__main__":
    unittest.main()
