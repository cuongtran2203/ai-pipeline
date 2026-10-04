#!/usr/bin/env python3
"""Wave-4 (GB) tests cho `kg.py quarantine`: dry-run/--apply cạnh không hợp lệ, backup độc nhất,
chỉ chuyển cạnh sai, validate sau đó VALID, và idempotent. Chạy stdlib, thư mục tạm.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import kg  # noqa: E402
import statefile  # noqa: E402


def run_cli(script, *args):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT)


def raw_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_bad_graph(run):
    """Graph hợp lệ một phần: 1 cạnh `uses` đúng, 1 cạnh `evidenced_by` sai kiểu cả hai đầu."""
    os.makedirs(os.path.join(run, "knowledge"), exist_ok=True)
    with open(kg.entities_path(run), "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "task:T1", "type": "Task", "title": "T1"}) + "\n")
        f.write(json.dumps({"id": "artifact:runs/x/e.md", "type": "Artifact", "title": "e"}) + "\n")
    bad = {"source": "artifact:runs/x/e.md", "target": "task:T1", "type": "evidenced_by",
           "valid_from": None, "valid_to": None, "recorded_at": "2026-01-01 00:00",
           "source_ref": None, "confidence": 1.0}
    good = {"source": "task:T1", "target": "artifact:runs/x/e.md", "type": "uses",
            "valid_from": None, "valid_to": None, "recorded_at": "2026-01-01 00:00",
            "source_ref": None, "confidence": 1.0}
    with open(kg.edges_path(run), "w", encoding="utf-8") as f:
        f.write(json.dumps(bad) + "\n")
        f.write(json.dumps(good) + "\n")


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp(prefix="kg-quar-")
        self.addCleanup(shutil.rmtree, self.run, ignore_errors=True)

    def test_dry_run_reports_and_modifies_nothing(self):
        write_bad_graph(self.run)
        with open(kg.edges_path(self.run), "rb") as f:
            before = f.read()
        r = run_cli("kg.py", "quarantine", self.run)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DRY-RUN", r.stdout)
        self.assertIn("evidenced_by", r.stdout)
        with open(kg.edges_path(self.run), "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertFalse(os.path.exists(kg.quarantine_path(self.run)))
        self.assertEqual(glob.glob(os.path.join(kg.kg_dir(self.run), "*.bak-*")), [])

    def test_apply_moves_only_invalid_edge_and_validates(self):
        write_bad_graph(self.run)
        r = run_cli("kg.py", "quarantine", self.run, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("VALID", r.stdout)

        # Chỉ 1 cạnh sai bị chuyển, giữ nguyên nội dung + lý do + thời điểm.
        q = raw_lines(kg.quarantine_path(self.run))
        self.assertEqual(len(q), 1, q)
        rec = q[0]
        self.assertEqual(rec["edge"]["source"], "artifact:runs/x/e.md")
        self.assertEqual(rec["edge"]["target"], "task:T1")
        self.assertEqual(rec["edge"]["type"], "evidenced_by")
        self.assertEqual(rec["edge"]["recorded_at"], "2026-01-01 00:00")
        self.assertTrue(rec["reason"])
        self.assertTrue(any("evidenced_by" in x for x in rec["reason"]), rec["reason"])
        self.assertTrue(rec["quarantined_at"])

        # Cạnh hợp lệ còn nguyên, cạnh sai đã rời edges.jsonl.
        edges = raw_lines(kg.edges_path(self.run))
        self.assertEqual(len(edges), 1)
        self.assertEqual((edges[0]["source"], edges[0]["type"]), ("task:T1", "uses"))
        self.assertEqual(run_cli("kg.py", "validate", self.run).returncode, 0)

        # Backup entities + edges với tên độc nhất.
        backups = glob.glob(os.path.join(kg.kg_dir(self.run), "entities.jsonl.bak-*")) + \
            glob.glob(os.path.join(kg.kg_dir(self.run), "edges.jsonl.bak-*"))
        self.assertEqual(len(backups), 2, backups)
        self.assertEqual(len(set(backups)), 2, "tên backup phải độc nhất")

    def test_apply_is_idempotent(self):
        write_bad_graph(self.run)
        self.assertEqual(run_cli("kg.py", "quarantine", self.run, "--apply").returncode, 0)
        watch = [kg.entities_path(self.run), kg.edges_path(self.run), kg.quarantine_path(self.run)]
        snap = {}
        for p in watch:
            with open(p, "rb") as f:
                snap[p] = f.read()
        r2 = run_cli("kg.py", "quarantine", self.run, "--apply")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("không có cạnh không hợp lệ", r2.stdout)
        for p in watch:
            with open(p, "rb") as f:
                self.assertEqual(f.read(), snap[p], f"{p} đổi khi chạy quarantine lần hai")
        self.assertEqual(len(raw_lines(kg.quarantine_path(self.run))), 1)

    def test_v2_knowledge_copy_quarantine_then_valid_and_idempotent(self):
        src = os.path.join(ROOT, "runs", "ai-pipeline-v2", "knowledge")
        if not os.path.isdir(src):
            self.skipTest("không có knowledge run v2")
        shutil.copytree(src, os.path.join(self.run, "knowledge"),
                        ignore=shutil.ignore_patterns("*.lock"))
        # dry-run trước, không sửa
        self.assertEqual(run_cli("kg.py", "quarantine", self.run).returncode, 0)
        edges_before = len(raw_lines(kg.edges_path(self.run)))
        # apply trên BẢN SAO (không đụng run thật)
        r = run_cli("kg.py", "quarantine", self.run, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        v = run_cli("kg.py", "validate", self.run)
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("VALID", v.stdout)
        # Chỉ bớt cạnh, không thêm
        self.assertLessEqual(len(raw_lines(kg.edges_path(self.run))), edges_before)
        q1 = raw_lines(kg.quarantine_path(self.run))
        # chạy lần hai không đổi
        self.assertEqual(run_cli("kg.py", "quarantine", self.run, "--apply").returncode, 0)
        self.assertEqual(len(raw_lines(kg.quarantine_path(self.run))), len(q1))


class KgStrictReaderTests(unittest.TestCase):
    def test_read_edges_rejects_mid_file_corruption(self):
        """Reader KG dùng strict: dòng hỏng ở GIỮA edges.jsonl phải báo lỗi, không bỏ qua (RV4)."""
        run = tempfile.mkdtemp(prefix="kg-strict-")
        self.addCleanup(shutil.rmtree, run, ignore_errors=True)
        os.makedirs(os.path.join(run, "knowledge"))
        with open(kg.edges_path(run), "w", encoding="utf-8") as f:
            f.write(json.dumps({"source": "a", "target": "b", "type": "uses"}) + "\n")
            f.write("khong-phai-json\n")
            f.write(json.dumps({"source": "c", "target": "d", "type": "uses"}) + "\n")
        # mặc định cũ vẫn bỏ qua để tương thích
        self.assertEqual(len(statefile.read_jsonl(kg.edges_path(run))), 2)
        with self.assertRaises(statefile.StateCorrupt):
            kg.read_edges(run)
        with self.assertRaises(statefile.StateCorrupt):
            kg.collect_validation(run)


if __name__ == "__main__":
    unittest.main()
