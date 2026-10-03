#!/usr/bin/env python3
"""Mark a plan task done after the coordinator verified `worker_done` (outcome succeeded + acceptance met).

Usage: settle_task.py <run_dir> <orca_task_id>     (task id = payload.taskId of the worker_done message)
Maps the Orca task id back to the plan id through task_map.json and appends it to done.json.
Do NOT call it for failed outcomes or artifacts that miss the acceptance line.
"""
import json
import os
import sys

run_dir, task = sys.argv[1], sys.argv[2]
tm = json.load(open(os.path.join(run_dir, "task_map.json"), encoding="utf-8"))
ids = [k for k, v in tm.items() if v == task]
if not ids:
    sys.exit(f"unknown orca task id {task} for {run_dir}")
p = os.path.join(run_dir, "done.json")
done = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []
if ids[0] not in done:
    done.append(ids[0])
json.dump(done, open(p, "w", encoding="utf-8"))
print("done:", ids[0], done)
