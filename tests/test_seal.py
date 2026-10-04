#!/usr/bin/env python3
"""Unittest stdlib cho scripts/seal.py.

Phu: dev/module-dev/analyst bi tu choi; integrator truoc lock bi tu choi, sau lock duoc cap;
hash lech (sua file nhan) bi tu choi; mo lan hai gan co exploratory; audit append-only
khong mat ban ghi khi hai tien trinh grant dong thoi; manifest khong chua nhan.
Chay: python -m unittest discover -s tests -v
"""
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import seal  # noqa: E402
import statefile  # noqa: E402


def _grant_worker(run_dir, task, purpose, q):
    here = os.path.dirname(os.path.abspath(__file__))
    scripts = os.path.abspath(os.path.join(here, "..", "scripts"))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import seal as seal_mod
    try:
        res = seal_mod.grant(run_dir, "integrator", task, purpose)
        q.put((task, res["granted"], res["reason"]))
    except Exception as e:  # pragma: no cover
        q.put((task, False, repr(e)))


class SealTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="seal-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.run_dir = os.path.join(self.dir, "run1")
        os.makedirs(self.run_dir)
        self.ids = os.path.join(self.dir, "ids.txt")
        self.labels = os.path.join(self.dir, "labels.txt")
        with open(self.ids, "w", encoding="utf-8") as f:
            f.write("id1\nid2\nid3\n")
        with open(self.labels, "w", encoding="utf-8") as f:
            f.write("LABEL_ALPHA\nLABEL_BETA\nLABEL_GAMMA\n")
        self.manifest = seal.make_manifest(self.run_dir, "ds-v1", "test-v1", self.ids, self.labels)

    def _lock(self):
        thr = os.path.join(self.dir, "threshold.json")
        with open(thr, "w", encoding="utf-8") as f:
            json.dump({"threshold": 0.5}, f)
        return seal.lock_recipe(self.run_dir, "model-v1", thr)

    def _audit(self):
        return statefile.read_jsonl(os.path.join(self.run_dir, "seal_audit.jsonl"))

    def test_manifest_has_expected_fields_and_no_labels(self):
        path = os.path.join(self.run_dir, "eval_manifest.json")
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        for token in ("LABEL_ALPHA", "LABEL_BETA", "LABEL_GAMMA"):
            self.assertNotIn(token, raw)
        man = json.loads(raw)
        self.assertEqual(man["dataset_version"], "ds-v1")
        self.assertEqual(man["split_id"], "test-v1")
        self.assertEqual(man["n_samples"], 3)
        self.assertEqual(len(man["ids_sha256"]), 64)
        self.assertEqual(len(man["labels_sha256"]), 64)
        self.assertTrue(man["labels_path"])

    def test_verify_ok_when_untouched(self):
        self.assertTrue(seal.verify(self.run_dir)["ok"])

    def test_dev_denied(self):
        self._lock()
        res = seal.grant(self.run_dir, "module-dev", "MOD-1", "train eval")
        self.assertFalse(res["granted"])
        self.assertEqual(res["reason"], "role_not_permitted")
        audit = self._audit()
        self.assertEqual(audit[-1]["result"], "denied")
        self.assertEqual(audit[-1]["role"], "module-dev")

    def test_analyst_denied(self):
        self._lock()
        res = seal.grant(self.run_dir, "data-analyst", "DA-1", "profile")
        self.assertFalse(res["granted"])
        self.assertEqual(res["reason"], "role_not_permitted")

    def test_integrator_before_lock_denied(self):
        res = seal.grant(self.run_dir, "integrator", "INT-1", "final eval")
        self.assertFalse(res["granted"])
        self.assertEqual(res["reason"], "recipe_not_locked")

    def test_integrator_after_lock_granted_with_path(self):
        self._lock()
        res = seal.grant(self.run_dir, "integrator", "INT-1", "final eval")
        self.assertTrue(res["granted"])
        self.assertTrue(os.path.isfile(res["labels_path"]))
        self.assertFalse(res["exploratory"])

    def test_labels_hash_mismatch_denied(self):
        self._lock()
        with open(self.labels, "a", encoding="utf-8") as f:
            f.write("LABEL_EXTRA\n")
        self.assertFalse(seal.verify(self.run_dir)["ok"])
        res = seal.grant(self.run_dir, "integrator", "INT-1", "final eval")
        self.assertFalse(res["granted"])
        self.assertEqual(res["reason"], "labels_hash_mismatch")

    def test_second_open_flagged_exploratory(self):
        self._lock()
        first = seal.grant(self.run_dir, "integrator", "INT-1", "final eval")
        second = seal.grant(self.run_dir, "integrator", "INT-1", "re-check")
        self.assertFalse(first["exploratory"])
        self.assertTrue(second["exploratory"])
        grants = [r for r in self._audit() if r["result"] == "granted"]
        self.assertEqual([g["exploratory"] for g in grants], [False, True])

    def test_concurrent_grants_audit_append_only(self):
        self._lock()
        q = multiprocessing.Queue()
        procs = [multiprocessing.Process(target=_grant_worker, args=(self.run_dir, "INT-%d" % i, "concurrent", q))
                 for i in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        self.assertTrue(all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs])
        results = [q.get(timeout=5) for _ in procs]
        self.assertTrue(all(r[1] for r in results), results)
        granted = [r for r in self._audit() if r["result"] == "granted"]
        self.assertEqual(len(granted), 4)
        self.assertEqual(sorted(r["task"] for r in granted), ["INT-0", "INT-1", "INT-2", "INT-3"])


if __name__ == "__main__":
    unittest.main()