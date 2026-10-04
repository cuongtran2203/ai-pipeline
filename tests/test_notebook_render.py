#!/usr/bin/env python3
"""Wave-4 regression tests (GB) cho notebook: rebuild chụp snapshot trong render lock,
hợp đồng UUID của ID, và reconcile giữ key pending do writer khác thêm trong lúc sync.

Mỗi test tái hiện lỗi RV4 (fail trước khi sửa, pass sau). Chạy:
  python -m unittest discover -s tests -v      (stdlib only, thư mục tạm, không Orca)
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import kg  # noqa: E402
import notebook  # noqa: E402
import statefile  # noqa: E402


def append_entry(run, title, body, ts="2026-01-01 10:00"):
    """Ghi 1 mục journal có `id` (dưới khóa journal) — mô phỏng notebook.py log không rebuild."""
    e = {"ts": ts, "type": "experiment", "title": title, "body": body, "author": "test",
         "tags": [], "metrics": {}, "refs": []}
    with statefile.file_lock(notebook._journal_guard(run)):
        existing = notebook._read_raw_entries(run)
        e["id"] = notebook.new_entry_id(run, len(existing), e)
        statefile.append_jsonl(notebook.journal_path(run), e)
    return e


def read_journal_md(run):
    with open(os.path.join(run, "notebook", "journal.md"), encoding="utf-8") as f:
        return f.read()


class TempNotebook(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp(prefix="nb-render-")
        self.addCleanup(shutil.rmtree, self.run, ignore_errors=True)
        notebook.cmd_init(type("A", (), {"run_dir": self.run, "title": None})())


class RebuildRaceTests(TempNotebook):
    def test_rebuild_snapshot_under_lock_never_loses_committed_entry(self):
        """Hai rebuild: rebuild 'cũ' chụp snapshot rồi bị giữ lại; rebuild 'mới' ghi trước.

        Nếu snapshot chụp NGOÀI render lock (lỗi RV4), rebuild cũ ghi đè và journal.md mất mục mới.
        Với fix (snapshot bên trong lock), rebuild mới chỉ đọc journal sau khi rebuild cũ nhả khóa.
        """
        append_entry(self.run, "mục gốc", "nội dung gốc")

        first_snapshot = threading.Event()
        proceed = threading.Event()
        guard = threading.Lock()
        state = {"first": True}
        original = notebook.ensure_journal_ids

        def wrapped(run_dir):
            es = original(run_dir)
            with guard:
                is_first = state["first"]
                if is_first:
                    state["first"] = False
            if is_first:
                first_snapshot.set()
                proceed.wait(20)
            return es

        notebook.ensure_journal_ids = wrapped
        t_old = threading.Thread(target=notebook.rebuild, args=(self.run,))
        t_new = None
        try:
            t_old.start()
            self.assertTrue(first_snapshot.wait(10), "rebuild 'cũ' không chụp được snapshot")
            # Writer khác log mục mới trong lúc rebuild cũ đang giữ snapshot.
            append_entry(self.run, "mục mới", "nội dung thêm trong lúc race")
            t_new = threading.Thread(target=notebook.rebuild, args=(self.run,))
            t_new.start()
            # Không fix: rebuild mới xong ngay. Có fix: nó chờ render lock -> không xong trong 2s.
            t_new.join(2.0)
            proceed.set()
            t_old.join(20)
            t_new.join(20)
        finally:
            proceed.set()
            notebook.ensure_journal_ids = original

        self.assertFalse(t_old.is_alive(), "rebuild 'cũ' chưa kết thúc")
        self.assertFalse(t_new.is_alive(), "rebuild 'mới' chưa kết thúc")
        md = read_journal_md(self.run)
        self.assertIn("nội dung gốc", md)
        self.assertIn("nội dung thêm trong lúc race", md,
                      "journal.md mất mục mới: rebuild cũ ghi đè rebuild mới")


class NotebookIdContractTests(TempNotebook):
    def test_same_ordinal_title_body_minute_yields_distinct_ids(self):
        """Hai mục cùng ordinal/title/body/phút PHẢI ra ID khác nhau (nhờ UUID).

        Mutation bỏ UUID khỏi new_entry_id làm test này fail (các ID trùng nhau).
        """
        e = {"ts": "2026-01-01 10:00", "type": "experiment", "title": "Tiêu đề", "body": "nội dung",
             "author": "test", "tags": [], "metrics": {}, "refs": []}
        ids = {notebook.new_entry_id(self.run, 0, e) for _ in range(50)}
        self.assertEqual(len(ids), 50,
                         "ID notebook phải độc lập với (ordinal, title, body, phút); thiếu UUID")

    def test_kg_node_ids_stay_distinct_for_identical_entries(self):
        e1 = append_entry(self.run, "Trùng", "y hệt")
        e2 = append_entry(self.run, "Trùng", "y hệt")
        self.assertNotEqual(e1["id"], e2["id"])
        self.assertTrue(notebook.sync_to_kg(self.run, e1))
        self.assertTrue(notebook.sync_to_kg(self.run, e2))
        exps = [x["id"] for x in kg.read_entities(self.run).values() if x["type"] == "Experiment"]
        self.assertEqual(len(exps), 2)
        self.assertEqual(len(set(exps)), 2)


class NotebookStrictReaderTests(TempNotebook):
    def test_read_raw_entries_rejects_mid_file_corruption(self):
        """Journal đọc strict: dòng hỏng ở GIỮA file phải báo lỗi, không che mất bản ghi (RV4)."""
        append_entry(self.run, "hợp lệ", "a")
        jp = notebook.journal_path(self.run)
        with open(jp, "a", encoding="utf-8") as f:
            f.write("khong-phai-json\n")
            f.write(json.dumps({"id": "x", "ts": "2026-01-01 10:00", "type": "experiment",
                                "title": "sau lỗi", "body": "b"}) + "\n")
        with self.assertRaises(statefile.StateCorrupt):
            notebook._read_raw_entries(self.run)


class NotebookReconcileTests(TempNotebook):
    def test_reconcile_keeps_pending_added_during_sync(self):
        """Reconcile chỉ được xoá key đã xử lý; key writer khác thêm trong lúc sync phải giữ."""
        entry = append_entry(self.run, "E", "body")
        pending_path = os.path.join(self.run, "notebook", "sync_pending.json")
        statefile.update_json(pending_path,
                              lambda d: {entry["id"]: {"error": "cũ", "ts": "old"}}, default={})

        sync_entered = threading.Event()
        release_sync = threading.Event()
        original = notebook._sync_entry

        def fake_sync(run_dir, e, kg_edges_arg=None):
            sync_entered.set()
            self.assertTrue(release_sync.wait(10), "writer không kịp thêm pending")

        notebook._sync_entry = fake_sync
        result = {}

        def do_reconcile():
            result["rc"] = notebook.cmd_reconcile(type("A", (), {"run_dir": self.run})())

        t = threading.Thread(target=do_reconcile)
        try:
            t.start()
            self.assertTrue(sync_entered.wait(10), "reconcile không bắt đầu sync")
            # Writer khác thêm pending MỚI ngay giữa lúc reconcile đang sync.
            statefile.update_json(pending_path,
                                  lambda d: {**(d or {}), "new-entry": {"error": "mới", "ts": "now"}},
                                  default={})
            release_sync.set()
            t.join(20)
        finally:
            release_sync.set()
            notebook._sync_entry = original

        self.assertEqual(result.get("rc"), 0)
        data = statefile.read_json(pending_path, {})
        self.assertNotIn(entry["id"], data, "key đã sync xong phải được xoá")
        self.assertIn("new-entry", data, "key thêm trong lúc sync bị xoá mất (lỗi RV4)")


if __name__ == "__main__":
    unittest.main()
