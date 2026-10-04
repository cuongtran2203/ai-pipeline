#!/usr/bin/env python3
"""Unittest stdlib cho scripts/seal.py (wave 4: siet seal).

Phu: manifest khong chua nhan va khong ro ri duong dan tuyet doi; verify hash; role lay TU PLAN
(dev bi tu choi du khai --role integrator), task khong co trong plan, khong co plan; recipe lock
atomic (nhieu tien trinh chi mot thang); verify threshold luc grant (sua threshold sau lock -> tu choi);
grant atomic (barrier, nhieu tien trinh: dung MOT blind-final); audit chuoi hash phat hien xoa/sua/cat;
path nhan ngoai goc checkout qua --labels-dir/SEAL_LABELS_ROOT.
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


def _ensure_path():
    here = os.path.dirname(os.path.abspath(__file__))
    scripts = os.path.abspath(os.path.join(here, "..", "scripts"))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def _seal_grant_worker(run_dir, task, purpose, role, barrier, q):
    _ensure_path()
    import seal as seal_mod
    try:
        barrier.wait(timeout=30)
    except Exception:
        pass
    try:
        res = seal_mod.grant(run_dir, role, task, purpose)
        q.put({"task": task, "granted": res["granted"], "exploratory": res["exploratory"], "reason": res["reason"]})
    except Exception as e:  # pragma: no cover
        q.put({"task": task, "granted": False, "exploratory": None, "reason": repr(e)})


def _seal_lock_worker(run_dir, threshold_file, barrier, q):
    _ensure_path()
    import seal as seal_mod
    try:
        barrier.wait(timeout=30)
    except Exception:
        pass
    try:
        seal_mod.lock_recipe(run_dir, "model-v1", threshold_file)
        q.put(("ok", None))
    except Exception as e:
        q.put(("err", type(e).__name__))


class SealTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="seal-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.run_dir = os.path.join(self.dir, "run1")
        os.makedirs(self.run_dir)
        self.ids = os.path.join(self.dir, "ids.txt")
        self.labels_dir = os.path.join(self.dir, "labels")
        os.makedirs(self.labels_dir)
        self.labels = os.path.join(self.labels_dir, "labels.txt")
        with open(self.ids, "w", encoding="utf-8") as f:
            f.write("id1\nid2\nid3\n")
        with open(self.labels, "w", encoding="utf-8") as f:
            f.write("LABEL_ALPHA\nLABEL_BETA\nLABEL_GAMMA\n")
        self._write_plan(self.run_dir)
        self.manifest = seal.make_manifest(self.run_dir, "ds-v1", "test-v1", self.ids, self.labels,
                                           labels_root=self.labels_dir)

    def _write_plan(self, run_dir):
        tasks = [{"id": "INT-%d" % i, "role": "integrator"} for i in range(1, 5)]
        tasks += [{"id": "EVAL-1", "role": "evaluator"},
                  {"id": "MOD-1", "role": "module-dev"},
                  {"id": "DA-1", "role": "data-analyst"},
                  {"id": "G1", "kind": "gate"}]
        with open(os.path.join(run_dir, "plan.json"), "w", encoding="utf-8") as f:
            json.dump({"run_id": "run1", "title": "t", "tasks": tasks}, f)

    def _write_threshold(self):
        thr = os.path.join(self.dir, "threshold.json")
        with open(thr, "w", encoding="utf-8") as f:
            json.dump({"threshold": 0.5}, f)
        return thr

    def _lock(self):
        return seal.lock_recipe(self.run_dir, "model-v1", self._write_threshold())

    def _audit_path(self):
        return os.path.join(self.run_dir, "seal_audit.jsonl")

    def _audit(self):
        with open(self._audit_path(), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _audit_raw_lines(self):
        with open(self._audit_path(), encoding="utf-8") as f:
            return [line for line in f.read().splitlines() if line.strip()]

    def _rewrite_audit(self, lines):
        with open(self._audit_path(), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

    # --- manifest / verify ---

    def test_manifest_has_expected_fields_and_no_labels(self):
        with open(os.path.join(self.run_dir, "eval_manifest.json"), encoding="utf-8") as f:
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

    def test_manifest_does_not_leak_absolute_outside_path(self):
        with open(os.path.join(self.run_dir, "eval_manifest.json"), encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn(self.dir, raw)
        self.assertNotIn(self.dir.replace("\\", "/"), raw)
        self.assertFalse(os.path.isabs(self.manifest["labels_path"]))
        self.assertFalse(os.path.isabs(self.manifest["labels_root"]))

    def test_labels_outside_root_without_root_raises(self):
        run2 = os.path.join(self.dir, "run-no-root")
        os.makedirs(run2)
        with self.assertRaises(seal.SealError):
            seal.make_manifest(run2, "ds-v1", "test-v1", self.ids, self.labels)

    def test_labels_root_env_token_resolves(self):
        os.environ["SEAL_LABELS_ROOT"] = self.labels_dir
        self.addCleanup(lambda: os.environ.pop("SEAL_LABELS_ROOT", None))
        run2 = os.path.join(self.dir, "run-env")
        os.makedirs(run2)
        man = seal.make_manifest(run2, "ds-v1", "test-v1", self.ids, self.labels)
        self.assertEqual(man["labels_root"], seal.ENV_ROOT_TOKEN)
        res = seal.verify(run2)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["labels_path"], os.path.abspath(self.labels))

    def test_verify_ok_when_untouched(self):
        self.assertTrue(seal.verify(self.run_dir)["ok"])

    # --- role lay tu plan ---

    def test_dev_denied_even_claiming_integrator(self):
        self._lock()
        res = seal.grant(self.run_dir, "integrator", "MOD-1", "train eval")
        self.assertFalse(res["granted"])
        self.assertEqual(res["reason"], "role_mismatch")
        res2 = seal.grant(self.run_dir, None, "MOD-1", "train eval")
        self.assertFalse(res2["granted"])
        self.assertEqual(res2["reason"], "role_not_permitted")
        audit = self._audit()
        self.assertEqual(audit[-1]["role"], "module-dev")

    def test_analyst_denied(self):
        self._lock()
        res = seal.grant(self.run_dir, "integrator", "DA-1", "profile")
        self.assertFalse(res["granted"])
        self.assertEqual(res["reason"], "role_mismatch")
        res2 = seal.grant(self.run_dir, None, "DA-1", "profile")
        self.assertFalse(res2["granted"])
        self.assertEqual(res2["reason"], "role_not_permitted")

    def test_task_not_in_plan_denied(self):
        self._lock()
        res = seal.grant(self.run_dir, "integrator", "INT-999", "final eval")
        self.assertFalse(res["granted"])
        self.assertEqual(res["reason"], "task_not_in_plan")

    def test_no_plan_denied(self):
        run2 = os.path.join(self.dir, "run-noplan")
        os.makedirs(run2)
        seal.make_manifest(run2, "ds-v1", "test-v1", self.ids, self.labels, labels_root=self.labels_dir)
        seal.lock_recipe(run2, "model-v1", self._write_threshold())
        res = seal.grant(run2, "integrator", "INT-1", "final eval")
        self.assertFalse(res["granted"])
        self.assertEqual(res["reason"], "no_plan")

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
        self.assertEqual(res["labels_path"], os.path.abspath(self.labels))

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

    # --- recipe lock / threshold ---

    def test_threshold_changed_after_lock_denied(self):
        thr = self._write_threshold()
        seal.lock_recipe(self.run_dir, "model-v1", thr)
        with open(thr, "w", encoding="utf-8") as f:
            json.dump({"threshold": 0.9}, f)
        res = seal.grant(self.run_dir, "integrator", "INT-1", "final eval")
        self.assertFalse(res["granted"])
        self.assertEqual(res["reason"], "recipe_changed")

    def test_concurrent_lock_single_winner(self):
        thr = self._write_threshold()
        n = 3
        barrier = multiprocessing.Barrier(n)
        q = multiprocessing.Queue()
        procs = [multiprocessing.Process(target=_seal_lock_worker, args=(self.run_dir, thr, barrier, q))
                 for _ in range(n)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        self.assertTrue(all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs])
        results = [q.get(timeout=10) for _ in range(n)]
        winners = [r for r in results if r[0] == "ok"]
        self.assertEqual(len(winners), 1, results)
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "recipe_lock.json")))

    # --- grant atomic (barrier) ---

    def test_concurrent_first_grant_single_blind_final(self):
        self._lock()
        n = 4
        barrier = multiprocessing.Barrier(n)
        q = multiprocessing.Queue()
        procs = [multiprocessing.Process(target=_seal_grant_worker,
                                         args=(self.run_dir, "INT-%d" % i, "concurrent", "integrator", barrier, q))
                 for i in range(1, n + 1)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        self.assertTrue(all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs])
        results = [q.get(timeout=10) for _ in range(n)]
        self.assertTrue(all(r["granted"] for r in results), results)
        blind = [r for r in results if r["exploratory"] is False]
        self.assertEqual(len(blind), 1, results)
        granted = [r for r in self._audit() if r["result"] == "granted"]
        self.assertEqual(len(granted), n)
        self.assertTrue(seal.verify_audit(self.run_dir)["ok"])

    # --- audit integrity ---

    def test_verify_audit_ok_after_grants(self):
        self._lock()
        for _ in range(3):
            self.assertTrue(seal.grant(self.run_dir, "integrator", "INT-1", "eval")["granted"])
        res = seal.verify_audit(self.run_dir)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["count"], 3)

    def test_audit_deleted_middle_line_detected(self):
        self._lock()
        for _ in range(3):
            seal.grant(self.run_dir, "integrator", "INT-1", "eval")
        lines = self._audit_raw_lines()
        self.assertEqual(len(lines), 3)
        del lines[1]
        self._rewrite_audit(lines)
        res = seal.verify_audit(self.run_dir)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "chain_broken")

    def test_audit_modified_line_detected(self):
        self._lock()
        seal.grant(self.run_dir, "integrator", "INT-1", "eval")
        records = self._audit()
        records[0]["purpose"] = "tampered-purpose"
        self._rewrite_audit([json.dumps(r, ensure_ascii=False) for r in records])
        res = seal.verify_audit(self.run_dir)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "record_tampered")

    def test_audit_truncated_tail_detected(self):
        self._lock()
        for _ in range(3):
            seal.grant(self.run_dir, "integrator", "INT-1", "eval")
        lines = self._audit_raw_lines()
        self._rewrite_audit(lines[:-1])
        res = seal.verify_audit(self.run_dir)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "truncated")

    def test_grant_refused_when_audit_tampered(self):
        self._lock()
        for _ in range(3):
            seal.grant(self.run_dir, "integrator", "INT-1", "eval")
        lines = self._audit_raw_lines()
        self._rewrite_audit(lines[:-1])
        before = len(self._audit_raw_lines())
        res = seal.grant(self.run_dir, "integrator", "INT-1", "eval")
        self.assertFalse(res["granted"])
        self.assertEqual(res["reason"], "audit_not_intact")
        self.assertEqual(len(self._audit_raw_lines()), before)


if __name__ == "__main__":
    unittest.main()
