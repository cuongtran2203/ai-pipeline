#!/usr/bin/env python3
"""Wave-3 regression tests (FE): KG/notebook/settle transactional writes + strict allow_dangling.

Covers, with a reproducing test each (fail before the fix, pass after):
  (1) kg.py: allow_dangling only relaxes a MISSING endpoint; a wrong-type existing endpoint is
      always rejected; entity/edge writes take a graph-level lock (check-then-append atomic);
      concurrent duplicate upserts/edges stay idempotent; init/backfill is atomic.
  (2) settle_task.py: done.json via statefile (idempotent by plan id, list enforced); a KG failure
      keeps done.json intact and records a pending sync; reconcile retries it.
  (3) notebook.py: id+append under the journal lock (no shared ordinal, no lost markdown); legacy
      entries are migrated once with a journal.jsonl.bak-<ts> backup; sync failures are recorded.
  (4) refs: project-root normalization (path alias -> one Artifact id), POSIX/Windows input,
      out-of-root refs rejected.

Run: python -m unittest discover -s tests -v   (stdlib only, temp dirs, no Orca worker start)
"""
import glob
import json
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import kg  # noqa: E402
import notebook  # noqa: E402
import settle_task  # noqa: E402

# --- multiprocessing worker functions (module level so Windows spawn can pickle them) ---


def _upsert_stress(run, tag, n):
    import kg as _kg
    _kg.upsert_entity(run, "task:shared", "Task", "Shared")
    _kg.upsert_entity(run, "artifact:runs/x/a.json", "Artifact", "a")
    for i in range(n):
        _kg.upsert_entity(run, f"exp:{tag}-{i}", "Experiment", f"{tag}-{i}")
        _kg.add_edge_checked(run, "task:shared", "artifact:runs/x/a.json", "uses")


def _hold_graph_lock(run, seconds, ready):
    import kg as _kg
    with _kg._graph_lock(run):
        ready.set()
        time.sleep(seconds)


def _nb_log_stress(run, tag, n, no_kg):
    import argparse
    import notebook as _nb
    for i in range(n):
        ns = argparse.Namespace(run_dir=run, type="experiment", title="Tiêu đề chung",
                                body=f"{tag}-{i}", author="module-dev", tags="", metrics="",
                                refs="", no_kg=no_kg, kg_edges=None)
        _nb.cmd_log(ns)


def _hold_journal_lock(run, seconds, ready):
    import notebook as _nb
    from statefile import file_lock
    with file_lock(_nb._journal_guard(run)):
        ready.set()
        time.sleep(seconds)


def run_cli(script, *args):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT)


def write_plan(run, tasks):
    with open(os.path.join(run, "plan.json"), "w", encoding="utf-8") as f:
        json.dump({"run_id": os.path.basename(run), "title": "t", "tasks": tasks}, f, ensure_ascii=False)


def raw_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_json_file(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


class TempRun(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp(prefix="fe-")
        self.addCleanup(shutil.rmtree, self.run, ignore_errors=True)


class KgStrictAllowDanglingTests(TempRun):
    def test_allow_dangling_rejects_wrong_type_but_relaxes_missing(self):
        kg.upsert_entity(self.run, "artifact:runs/x/a.json", "Artifact", "a")
        kg.upsert_entity(self.run, "task:T", "Task", "T")
        # Existing endpoint with the wrong type: never relaxable, even with allow_dangling=True.
        with self.assertRaises(kg.KgError):
            kg.add_edge_checked(self.run, "artifact:runs/x/a.json", "task:T", "evidenced_by",
                                allow_dangling=True)
        # Missing endpoint: the only case allow_dangling may relax (ordered backfill).
        kg.add_edge_checked(self.run, "task:T", "artifact:missing.json", "uses", allow_dangling=True)
        edges = kg.read_edges(self.run)
        self.assertEqual(len(edges), 1)
        self.assertEqual((edges[0]["source"], edges[0]["type"]), ("task:T", "uses"))

    def test_wrong_type_edge_raises_without_allow_dangling_too(self):
        kg.upsert_entity(self.run, "artifact:runs/x/a.json", "Artifact", "a")
        kg.upsert_entity(self.run, "task:T", "Task", "T")
        with self.assertRaises(kg.KgError):
            kg.add_edge_checked(self.run, "artifact:runs/x/a.json", "task:T", "evidenced_by")


class KgGraphLockTests(TempRun):
    def test_upsert_waits_for_graph_lock_held_by_another_process(self):
        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        proc = ctx.Process(target=_hold_graph_lock, args=(self.run, 1.2, ready))
        proc.start()
        try:
            self.assertTrue(ready.wait(10), "child did not take the graph lock")
            t0 = time.monotonic()
            kg.upsert_entity(self.run, "task:T", "Task", "T")
            elapsed = time.monotonic() - t0
        finally:
            proc.join(30)
        self.assertEqual(proc.exitcode, 0)
        # The write had to wait for the lock: proves check-then-append is guarded.
        self.assertGreater(elapsed, 0.4, "upsert_entity did not wait for the graph lock")

    def test_add_edge_waits_for_graph_lock_held_by_another_process(self):
        kg.upsert_entity(self.run, "task:T", "Task", "T")
        kg.upsert_entity(self.run, "artifact:runs/x/a.json", "Artifact", "a")
        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        proc = ctx.Process(target=_hold_graph_lock, args=(self.run, 1.2, ready))
        proc.start()
        try:
            self.assertTrue(ready.wait(10), "child did not take the graph lock")
            t0 = time.monotonic()
            kg.add_edge_checked(self.run, "task:T", "artifact:runs/x/a.json", "uses")
            elapsed = time.monotonic() - t0
        finally:
            proc.join(30)
        self.assertEqual(proc.exitcode, 0)
        self.assertGreater(elapsed, 0.4, "add_edge_checked did not wait for the graph lock")

    def test_concurrent_upserts_and_edges_stay_idempotent(self):
        ctx = multiprocessing.get_context("spawn")
        workers, per = 3, 6
        procs = [ctx.Process(target=_upsert_stress, args=(self.run, f"w{i}", per)) for i in range(workers)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        self.assertTrue(all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs])

        ents = raw_lines(kg.entities_path(self.run))
        ids = [e["id"] for e in ents]
        self.assertEqual(ids.count("task:shared"), 1, "duplicate Task entity from concurrent upserts")
        self.assertEqual(ids.count("artifact:runs/x/a.json"), 1)
        exps = [i for i in ids if i.startswith("exp:")]
        self.assertEqual(len(exps), workers * per)
        self.assertEqual(len(set(exps)), workers * per)
        edges = [e for e in raw_lines(kg.edges_path(self.run))
                 if (e["source"], e["target"], e["type"]) == ("task:shared", "artifact:runs/x/a.json", "uses")]
        self.assertEqual(len(edges), 1, "duplicate edge from concurrent add_edge_checked")
        self.assertEqual(kg.collect_validation(self.run)[2], [])


class KgAtomicBackfillTests(TempRun):
    def test_backfill_is_atomic_and_leaves_no_scratch(self):
        src = os.path.join(self.run, "src")
        os.makedirs(src)
        out = os.path.join(self.run, "out")
        r = run_cli("kg.py", "backfill", src, "--out-dir", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        v = run_cli("kg.py", "validate", out)
        self.assertEqual(v.returncode, 0, v.stderr)
        leftovers = [n for n in os.listdir(out) if n.startswith(".backfill-")]
        self.assertEqual(leftovers, [], "scratch backfill dir not cleaned up")
        tmps = glob.glob(os.path.join(kg.kg_dir(out), "*.tmp"))
        self.assertEqual(tmps, [])


class SettleTaskTransactionalTests(TempRun):
    def _plan(self):
        write_plan(self.run, [
            {"id": "G2", "kind": "gate", "title": "Gate G2"},
            {"id": "T1", "kind": "worker", "title": "Task One",
             "inputs": ["runs/fetest/in.txt"],
             "outputs": ["runs/fetest/artifacts/T1/README.md"],
             "deps": ["G2"]},
        ])
        with open(os.path.join(self.run, "task_map.json"), "w", encoding="utf-8") as f:
            json.dump({"T1": "orca-t1"}, f)

    def test_settle_twice_is_idempotent(self):
        self._plan()
        for _ in range(2):
            r = run_cli("settle_task.py", self.run, "orca-t1")
            self.assertEqual(r.returncode, 0, r.stderr)
        done = read_json_file(os.path.join(self.run, "done.json"))
        self.assertEqual(done.count("T1"), 1, done)
        self.assertEqual(run_cli("kg.py", "validate", self.run).returncode, 0)

    def test_kg_failure_keeps_done_json_and_records_pending(self):
        self._plan()
        original = settle_task.kg.add_edge_checked

        def boom(*_a, **_k):
            raise kg.KgError("mô phỏng KG lỗi")

        settle_task.kg.add_edge_checked = boom
        try:
            settle_task.settle(self.run, "orca-t1")
        finally:
            settle_task.kg.add_edge_checked = original

        done = read_json_file(os.path.join(self.run, "done.json"))
        self.assertIn("T1", done)
        self.assertEqual(done.count("T1"), 1)
        pending = read_json_file(os.path.join(kg.kg_dir(self.run), "sync_pending.json"))
        self.assertIn("T1", pending)

        # reconcile with the real API clears the pending entry and produces a valid graph.
        self.assertEqual(settle_task.reconcile(self.run), 0)
        pending = read_json_file(os.path.join(kg.kg_dir(self.run), "sync_pending.json"))
        self.assertEqual(pending, {})
        self.assertEqual(run_cli("kg.py", "validate", self.run).returncode, 0)

    def test_done_json_non_list_is_refused(self):
        self._plan()
        with open(os.path.join(self.run, "done.json"), "w", encoding="utf-8") as f:
            json.dump({"T1": True}, f)
        r = run_cli("settle_task.py", self.run, "orca-t1")
        self.assertNotEqual(r.returncode, 0)
        with open(os.path.join(self.run, "done.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"T1": True})


class NotebookConcurrencyTests(TempRun):
    def test_two_workers_share_no_ordinal_and_lose_no_entry(self):
        ctx = multiprocessing.get_context("spawn")
        workers, per = 2, 4
        procs = [ctx.Process(target=_nb_log_stress, args=(self.run, f"w{i}", per, True)) for i in range(workers)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        self.assertTrue(all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs])

        entries = notebook.read_entries(self.run)
        self.assertEqual(len(entries), workers * per)
        self.assertEqual(len({e["id"] for e in entries}), workers * per)
        ordinals = [int(re.search(r"#(\d+)-", e["id"]).group(1)) for e in entries]
        self.assertEqual(len(set(ordinals)), workers * per, "two workers shared a journal ordinal")
        with open(os.path.join(self.run, "notebook", "journal.md"), encoding="utf-8") as f:
            journal_md = f.read()
        for w in range(workers):
            for i in range(per):
                self.assertIn(f"w{w}-{i}", journal_md)

    def test_log_waits_for_journal_lock_held_by_another_process(self):
        ctx = multiprocessing.get_context("spawn")
        notebook.cmd_init(type("A", (), {"run_dir": self.run, "title": None})())
        ready = ctx.Event()
        proc = ctx.Process(target=_hold_journal_lock, args=(self.run, 1.2, ready))
        proc.start()
        try:
            self.assertTrue(ready.wait(10), "child did not take the journal lock")
            t0 = time.monotonic()
            notebook.cmd_log(type("A", (), {
                "run_dir": self.run, "type": "experiment", "title": "T", "body": "b",
                "author": "a", "tags": "", "metrics": "", "refs": "", "no_kg": True, "kg_edges": None})())
            elapsed = time.monotonic() - t0
        finally:
            proc.join(30)
        self.assertEqual(proc.exitcode, 0)
        self.assertGreater(elapsed, 0.4, "cmd_log did not wait for the journal lock")


class NotebookMigrationTests(TempRun):
    def _legacy_journal(self, bodies=("giống hệt", "giống hệt")):
        d = os.path.join(self.run, "notebook")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "journal.jsonl"), "w", encoding="utf-8") as f:
            for b in bodies:
                f.write(json.dumps({"ts": "2026-01-01 10:00", "type": "experiment", "title": "Cũ",
                                    "body": b, "author": "module-dev", "tags": [], "metrics": {},
                                    "refs": []}, ensure_ascii=False) + "\n")

    def test_legacy_identical_entries_migrate_to_two_nodes_with_backup(self):
        self._legacy_journal()
        entries = notebook.ensure_journal_ids(self.run)
        self.assertEqual(len(entries), 2)
        self.assertEqual(len({e["id"] for e in entries}), 2)
        backups = glob.glob(os.path.join(self.run, "notebook", "journal.jsonl.bak-*"))
        self.assertEqual(len(backups), 1, "migration must keep a journal.jsonl.bak-<ts> backup")

        # Stable ids: a second call must not recompute them.
        again = [e["id"] for e in notebook.ensure_journal_ids(self.run)]
        self.assertEqual(again, [e["id"] for e in entries])

        # Two identical legacy entries become two distinct Experiment nodes (not merged).
        for e in entries:
            self.assertTrue(notebook.sync_to_kg(self.run, e))
        exps = [e for e in kg.read_entities(self.run).values() if e["type"] == "Experiment"]
        self.assertEqual(len(exps), 2)
        self.assertEqual(len({e["id"] for e in exps}), 2)
        self.assertEqual(run_cli("kg.py", "validate", self.run).returncode, 0)

    def test_notebook_sync_failure_records_pending(self):
        notebook.cmd_init(type("A", (), {"run_dir": self.run, "title": None})())
        original = notebook.kg.upsert_entity

        def boom(*_a, **_k):
            raise kg.KgError("mô phỏng KG lỗi")

        notebook.kg.upsert_entity = boom
        try:
            ok = notebook.sync_to_kg(self.run, {"id": "x#1", "ts": "2026-01-01 10:00", "type": "experiment",
                                                "title": "T", "body": "b", "author": "a",
                                                "tags": [], "metrics": {}, "refs": []})
        finally:
            notebook.kg.upsert_entity = original
        self.assertFalse(ok)
        pending = read_json_file(os.path.join(self.run, "notebook", "sync_pending.json"))
        self.assertIn("x#1", pending)


class RefNormalizationTests(TempRun):
    def test_path_alias_maps_to_one_artifact_id(self):
        self.assertEqual(kg.normalize_ref_path("runs/x/../y/a-v1.0.json"), "runs/y/a-v1.0.json")
        self.assertEqual(kg.normalize_ref_path("runs\\x\\..\\y\\a-v1.0.json"), "runs/y/a-v1.0.json")
        a = kg.upsert_artifact_ref(self.run, "runs/x/../y/a-v1.0.json")
        b = kg.upsert_artifact_ref(self.run, "runs/y/a-v1.0.json")
        self.assertEqual(a, b)
        arts = [e for e in raw_lines(kg.entities_path(self.run)) if e["type"] == "Artifact"]
        self.assertEqual(len(arts), 1, arts)

    def test_absolute_in_root_path_normalizes(self):
        abs_in = os.path.join(kg.PROJECT_ROOT, "runs", "z", "..", "z", "b-v0.2.json")
        self.assertEqual(kg.normalize_ref_path(abs_in), "runs/z/b-v0.2.json")

    def test_ref_outside_project_root_is_rejected(self):
        outside_rel = "../../etc/passwd"
        outside_abs = os.path.abspath(os.path.join(kg.PROJECT_ROOT, "..", "outside-fe", "a.json"))
        self.assertIsNone(kg.normalize_ref_path(outside_rel))
        self.assertIsNone(kg.normalize_ref_path(outside_abs))
        self.assertIsNone(kg.artifact_ref_id(outside_rel))
        with self.assertRaises(kg.KgError):
            kg.upsert_artifact_ref(self.run, outside_rel)
        self.assertEqual(raw_lines(kg.entities_path(self.run)), [])

    def test_notebook_rejects_out_of_root_ref_but_keeps_entry(self):
        notebook.cmd_init(type("A", (), {"run_dir": self.run, "title": None})())
        e = {"ts": "2026-01-01 10:00", "type": "experiment", "title": "Ref xấu", "body": "b",
             "author": "module-dev", "tags": [], "metrics": {}, "refs": ["../../secret.json"]}
        e["id"] = notebook.new_entry_id(self.run, 0, e)
        self.assertTrue(notebook.sync_to_kg(self.run, e))
        arts = [x for x in kg.read_entities(self.run).values() if x["type"] == "Artifact"]
        self.assertEqual(arts, [])


if __name__ == "__main__":
    unittest.main()
