#!/usr/bin/env python3
"""Unittest stdlib cho scripts/statefile.py.

Phu: race nhieu tien trinh (khong mat ban ghi), crash giua ghi temp va os.replace
(khong hong file, khong khoa mo coi), doc BOM UTF-8, bo qua dong cuoi ghi do.
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

import statefile  # noqa: E402


def _mixed_worker(jsonl_path, counter_path, n, tag):
    """N tien trinh chay: vua append_jsonl vua update_json tang bo dem."""
    for i in range(n):
        statefile.append_jsonl(jsonl_path, {"tag": tag, "i": i})
        statefile.update_json(counter_path, lambda d: {"n": (d or {}).get("n", 0) + 1}, default={"n": 0})


def _crash_during_replace(path):
    """Crash SAU khi ghi temp, TRUOC os.replace; os._exit bo qua cleanup."""
    statefile.os.replace = lambda *a, **k: os._exit(7)
    try:
        statefile.update_json(path, lambda d: {"n": (d or {}).get("n", 0) + 1}, default={"n": 0})
    except BaseException:
        os._exit(8)


class StateFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="statefile-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_read_json_missing_returns_default(self):
        self.assertEqual(statefile.read_json(os.path.join(self.dir, "nope.json"), {"d": 1}), {"d": 1})

    def test_read_json_accepts_utf8_bom(self):
        path = os.path.join(self.dir, "bom.json")
        with open(path, "w", encoding="utf-8-sig") as f:
            json.dump({"ten": "BOM"}, f, ensure_ascii=False)
        self.assertEqual(statefile.read_json(path), {"ten": "BOM"})

    def test_read_jsonl_accepts_bom_and_skips_partial_last_line(self):
        path = os.path.join(self.dir, "log.jsonl")
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(json.dumps({"a": 1}) + "\n")
            f.write(json.dumps({"a": 2}) + "\n")
            f.write('{"a": 3')  # dong cuoi bi cat do, khong co newline
        self.assertEqual(statefile.read_jsonl(path), [{"a": 1}, {"a": 2}])

    def test_read_jsonl_skips_garbage_lines(self):
        path = os.path.join(self.dir, "log.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"a": 1}\n')
            f.write('khong-phai-json\n')
            f.write('\n')
            f.write('{"a": 2}\n')
        self.assertEqual(statefile.read_jsonl(path), [{"a": 1}, {"a": 2}])

    def test_append_jsonl_writes_one_valid_json_per_line(self):
        path = os.path.join(self.dir, "log.jsonl")
        statefile.append_jsonl(path, {"x": 1})
        statefile.append_jsonl(path, {"y": "z"})
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        self.assertTrue(raw.endswith("\n"))
        lines = raw.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual([json.loads(x) for x in lines], [{"x": 1}, {"y": "z"}])

    def test_update_json_is_atomic_and_returns_new_value(self):
        path = os.path.join(self.dir, "state.json")
        out = statefile.update_json(path, lambda d: {**(d or {}), "k": 1}, default={})
        self.assertEqual(out, {"k": 1})
        statefile.update_json(path, lambda d: {**d, "k": 2})
        self.assertEqual(statefile.read_json(path), {"k": 2})

    def test_failed_replace_keeps_file_and_cleans_temp(self):
        path = os.path.join(self.dir, "state.json")
        statefile.update_json(path, lambda d: {"n": 1}, default={})
        real = statefile.os.replace

        def boom(*a, **k):
            raise OSError("mo phong replace loi")

        statefile.os.replace = boom
        try:
            with self.assertRaises(OSError):
                statefile.update_json(path, lambda d: {"n": 2})
        finally:
            statefile.os.replace = real
        self.assertEqual(statefile.read_json(path), {"n": 1})
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        statefile.update_json(path, lambda d: {"n": d["n"] + 1})
        self.assertEqual(statefile.read_json(path), {"n": 2})

    def test_crash_between_temp_and_replace_leaves_valid_file_and_free_lock(self):
        path = os.path.join(self.dir, "state.json")
        statefile.update_json(path, lambda d: {"n": 1}, default={})
        proc = multiprocessing.Process(target=_crash_during_replace, args=(path,))
        proc.start()
        proc.join(30)
        self.assertFalse(proc.is_alive(), "tien trinh con chua ket thuc")
        self.assertEqual(proc.exitcode, 7)
        self.assertEqual(statefile.read_json(path), {"n": 1})  # file chinh con nguyen
        statefile.update_json(path, lambda d: {"n": d["n"] + 1})  # khong bi khoa mo coi chan
        self.assertEqual(statefile.read_json(path), {"n": 2})

    def test_multiprocess_no_lost_records(self):
        jsonl = os.path.join(self.dir, "shared.jsonl")
        counter = os.path.join(self.dir, "counter.json")
        workers, per_worker = 4, 25
        procs = [multiprocessing.Process(target=_mixed_worker, args=(jsonl, counter, per_worker, "w%d" % i))
                 for i in range(workers)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        self.assertTrue(all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs])
        rows = statefile.read_jsonl(jsonl)
        self.assertEqual(len(rows), workers * per_worker)
        self.assertEqual(statefile.read_json(counter), {"n": workers * per_worker})
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    json.loads(line)

    def test_update_json_does_not_overwrite_corrupt_file(self):
        """Orchestrator review: a corrupt state file must raise, never be silently replaced by fn(default)."""
        bad = os.path.join(self.dir, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write('{"a": 1, ')
        with self.assertRaises(statefile.StateCorrupt):
            statefile.update_json(bad, lambda old: {"fresh": True}, default={})
        with open(bad, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"a": 1, ')

    def test_update_json_empty_or_missing_uses_default(self):
        missing = os.path.join(self.dir, "missing.json")
        self.assertEqual(statefile.update_json(missing, lambda old: old + ["x"], default=[]), ["x"])
        empty = os.path.join(self.dir, "empty.json")
        open(empty, "w", encoding="utf-8").close()
        self.assertEqual(statefile.update_json(empty, lambda old: old + [1], default=[]), [1])


if __name__ == "__main__":
    unittest.main()
