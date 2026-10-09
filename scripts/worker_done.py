#!/usr/bin/env python3
"""Giao thuc worker <-> coordinator tren Herdr (thay worker_done/ask cua Orca). Stdlib only.

  worker_done.py <run_dir> done --task <task_id> --outcome succeeded|failed [--report-path P] [--summary S]
  worker_done.py <run_dir> ask  --task <task_id> --question Q
  worker_done.py <run_dir> answer --ask <ask_id> --text T        (coordinator tra loi nguoi dung)

done -> <run_dir>/worker_done/<task_id>.json ; ask -> <run_dir>/asks/<task_id>-<n>.json.
Sau `ask`, worker DUNG (khong doan); pane se hien blocked trong Herdr. Coordinator doc ask qua
`herdr_rt.py check` va tra loi bang `answer` + `herdr pane send-text`.
"""
import argparse
import glob
import json
import os
import sys
import time


def _wr(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("op", choices=["done", "ask", "answer"])
    ap.add_argument("--task")
    ap.add_argument("--outcome", choices=["succeeded", "failed"])
    ap.add_argument("--report-path")
    ap.add_argument("--summary", default="")
    ap.add_argument("--question")
    ap.add_argument("--ask")
    ap.add_argument("--text")
    a = ap.parse_args(argv)
    if a.op == "done":
        if not (a.task and a.outcome):
            ap.error("done can --task va --outcome")
        _wr(os.path.join(a.run_dir, "worker_done", a.task + ".json"),
            {"task_id": a.task, "outcome": a.outcome, "report_path": a.report_path, "summary": a.summary,
             "at": int(time.time())})
        print(f"worker_done {a.task} {a.outcome}")
    elif a.op == "ask":
        if not (a.task and a.question):
            ap.error("ask can --task va --question")
        n = len(glob.glob(os.path.join(a.run_dir, "asks", a.task + "-*.json"))) + 1
        _wr(os.path.join(a.run_dir, "asks", f"{a.task}-{n}.json"),
            {"task_id": a.task, "question": a.question, "at": int(time.time())})
        print(f"asked {a.task}-{n}: DUNG va doi coordinator tra loi trong pane (khong doan).")
    else:
        if not (a.ask and a.text):
            ap.error("answer can --ask va --text")
        p = os.path.join(a.run_dir, "asks", a.ask + ".json")
        d = json.load(open(p, encoding="utf-8"))
        d["answer"] = a.text
        _wr(p, d)
        print(f"answered {a.ask}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
