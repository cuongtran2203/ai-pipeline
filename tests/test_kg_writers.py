#!/usr/bin/env python3
"""Regression tests for FB: every KG writer goes through one validated kg.py API,
stable notebook IDs/refs, and old-writer data is caught by validate/report.

Run: python -m unittest discover -s tests -v   (stdlib only, temp dirs)
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import kg  # noqa: E402
import notebook  # noqa: E402


def run_cli(script, *args):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT)


def write_plan(run_dir, tasks):
    with open(os.path.join(run_dir, "plan.json"), "w", encoding="utf-8") as f:
        json.dump({"run_id": "fbtest", "title": "t", "tasks": tasks}, f, ensure_ascii=False)


def read_entities(run_dir):
    p = os.path.join(run_dir, "knowledge", "entities.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class KgApiTests(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp(prefix="fbkg-")

    def test_add_edge_checked_rejects_wrong_endpoint(self):
        kg.upsert_entity(self.run, "task:t1", "Task", "T1")
        kg.upsert_entity(self.run, "exp:e1", "Experiment", "E1")
        # Old writer bug: Artifact -> Task evidenced_by (wrong direction/type).
        with self.assertRaises(kg.KgError):
            kg.add_edge_checked(self.run, "task:t1", "exp:e1", "evidenced_by")
        edges = kg.read_edges(self.run)
        self.assertEqual(edges, [])

    def test_add_edge_checked_rejects_unknown_type_and_dangling(self):
        with self.assertRaises(kg.KgError):
            kg.add_edge_checked(self.run, "task:t1", "task:t2", "produces")
        with self.assertRaises(kg.KgError):
            kg.add_edge_checked(self.run, "task:t1", "task:t2", "depends_on")


class SettleTaskTests(unittest.TestCase):
    def test_settle_task_writes_valid_graph_without_output_edge(self):
        run = tempfile.mkdtemp(prefix="fbsettle-")
        write_plan(run, [
            {"id": "G2", "kind": "gate", "title": "Gate G2"},
            {"id": "T1", "kind": "worker", "title": "Task One",
             "inputs": ["runs/fbtest/in.txt"],
             "outputs": ["runs/fbtest/artifacts/T1/README.md"],
             "deps": ["G2"]},
        ])
        with open(os.path.join(run, "task_map.json"), "w", encoding="utf-8") as f:
            json.dump({"T1": "task-t1"}, f)

        r = run_cli("settle_task.py", run, "task-t1")
        self.assertEqual(r.returncode, 0, r.stderr)

        v = run_cli("kg.py", "validate", run)
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("VALID", v.stdout)

        edges = kg.read_edges(run)
        kinds = {(e["source"], e["target"], e["type"]) for e in edges}
        self.assertIn(("task:T1", "task:G2", "depends_on"), kinds)
        self.assertIn(("task:T1", "artifact:runs/fbtest/in.txt", "uses"), kinds)
        # No "task produces artifact" edge: none of the 8 accepted types expresses it.
        self.assertFalse(any(e["target"] == "task:T1" and e["type"] == "evidenced_by" for e in edges))
        # Outputs are recorded on the Task entity instead.
        t1 = [e for e in read_entities(run) if e["id"] == "task:T1"][0]
        self.assertEqual(t1["properties"]["outputs"], ["runs/fbtest/artifacts/T1/README.md"])


class NotebookTests(unittest.TestCase):
    def test_duplicate_titles_get_unique_ids(self):
        run = tempfile.mkdtemp(prefix="fbnb-")
        for body in ("noi dung 1", "noi dung 2"):
            r = run_cli("notebook.py", "log", run, "--type", "experiment",
                        "--title", "Tiêu đề trùng", "--body", body, "--author", "module-dev",
                        "--refs", "runs/fbtest/artifacts/T1/README.md")
            self.assertEqual(r.returncode, 0, r.stderr)
        exps = [e["id"] for e in read_entities(run) if e["type"] == "Experiment"]
        self.assertEqual(len(exps), 2)
        self.assertEqual(len(set(exps)), 2, exps)
        v = run_cli("kg.py", "validate", run)
        self.assertEqual(v.returncode, 0, v.stderr)

    def test_refs_use_relative_path_not_basename(self):
        run = tempfile.mkdtemp(prefix="fbref-")
        r = run_cli("notebook.py", "log", run, "--type", "experiment", "--title", "E",
                    "--body", "b", "--author", "module-dev",
                    "--refs", "runs/fbtest/artifacts/T1/README.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        arts = [e for e in read_entities(run) if e["type"] == "Artifact"]
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["id"], "artifact:runs/fbtest/artifacts/T1/README.md")
        self.assertEqual(arts[0]["properties"]["path"], "runs/fbtest/artifacts/T1/README.md")
        self.assertFalse(any(e["id"] == "artifact:readmemd" for e in read_entities(run)))

    def test_old_journal_entry_without_id_is_readable(self):
        run = tempfile.mkdtemp(prefix="fbold-")
        os.makedirs(os.path.join(run, "notebook"), exist_ok=True)
        entry = {"ts": "2026-01-01 10:00", "type": "decision", "title": "Cũ", "body": "no id",
                 "author": "coordinator", "tags": [], "metrics": {}, "refs": ["runs/fbold/decisions.md"]}
        with open(os.path.join(run, "notebook", "journal.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        show = run_cli("notebook.py", "show", run)
        self.assertEqual(show.returncode, 0, show.stderr)
        self.assertIn("Cũ", show.stdout)
        exp = run_cli("notebook.py", "export", run)
        self.assertEqual(exp.returncode, 0, exp.stderr)

    def test_notebook_writes_only_through_kg_api(self):
        run = tempfile.mkdtemp(prefix="fbapi-")

        def boom(*_a, **_k):
            raise kg.KgError("blocked by test")

        orig_upsert, orig_edge = kg.upsert_entity, kg.add_edge_checked
        kg.upsert_entity, kg.add_edge_checked = boom, boom
        try:
            ns = argparse.Namespace(run_dir=run, type="experiment", title="X", body="y",
                                    author="z", tags="", metrics="", refs="", no_kg=False, kg_edges=None)
            notebook.cmd_log(ns)
        finally:
            kg.upsert_entity, kg.add_edge_checked = orig_upsert, orig_edge

        ep = os.path.join(run, "knowledge", "entities.jsonl")
        edp = os.path.join(run, "knowledge", "edges.jsonl")
        self.assertFalse(os.path.exists(ep) and os.path.getsize(ep) > 0)
        self.assertFalse(os.path.exists(edp) and os.path.getsize(edp) > 0)

    def test_writers_have_no_direct_jsonl_append(self):
        for name in ("settle_task.py", "notebook.py"):
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn("entities.jsonl", src, f"{name} must not touch entities.jsonl directly")
            self.assertNotIn("edges.jsonl", src, f"{name} must not touch edges.jsonl directly")


class ValidateReportTests(unittest.TestCase):
    def _bad_graph(self, run):
        os.makedirs(os.path.join(run, "knowledge"), exist_ok=True)
        with open(os.path.join(run, "knowledge", "entities.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "task:T1", "type": "Task", "title": "T1"}) + "\n")
        # Old-writer data: source artifact missing AND target type invalid for evidenced_by.
        edge = {"source": "artifact:runs/x/a.md", "target": "task:T1", "type": "evidenced_by",
                "valid_from": None, "valid_to": None, "recorded_at": "2026-01-01 00:00",
                "source_ref": None, "confidence": 1.0}
        with open(os.path.join(run, "knowledge", "edges.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(edge) + "\n")

    def test_validate_catches_wrong_type_even_when_endpoint_missing(self):
        run = tempfile.mkdtemp(prefix="fbval-")
        self._bad_graph(run)
        r = run_cli("kg.py", "validate", run)
        self.assertEqual(r.returncode, 1)
        self.assertIn("evidenced_by", r.stderr)
        self.assertIn("allows targets", r.stderr)

    def test_report_mode_is_read_only(self):
        run = tempfile.mkdtemp(prefix="fbrep-")
        self._bad_graph(run)
        edp = os.path.join(run, "knowledge", "edges.jsonl")
        with open(edp, "rb") as f:
            before = f.read()
        r = run_cli("kg.py", "report", run)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("evidenced_by", r.stdout)
        self.assertIn("REPORT-ONLY", r.stdout)
        with open(edp, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_examples_kg_sample_is_valid(self):
        r = run_cli("kg.py", "validate", os.path.join(ROOT, "examples", "kg.sample"))
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
