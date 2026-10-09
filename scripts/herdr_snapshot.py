#!/usr/bin/env python3
"""Read-only bridge: Herdr worker status -> workers.json for scripts/supervisor.py.

Usage: herdr_snapshot.py <run_dir> [--out FILE] [--include-settled]
       python scripts/herdr_snapshot.py runs/<id> --out runs/<id>/workers.json
       python scripts/supervisor.py runs/<id> --workers runs/<id>/workers.json

Reads <run_dir>/workers/*.json (written by herdr_rt.py worker-start), <run_dir>/worker_done/*.json and, per worker pane,
`herdr agent get <pane>` (read-only; status working|blocked|idle|done|unknown). Per worker: id = dispatchId, task = plan task id
(via task_map.json), state running|failed|done. last_heartbeat = now when Herdr reports the agent working/blocked/idle (live);
missing when Herdr cannot observe it (liveness `unverifiable`) — the supervisor reports that instead of guessing. Released and
succeeded workers are skipped unless --include-settled. Never starts, stops, closes or releases anything.
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import herdr_rt  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
LIVE = ("working", "blocked", "idle")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--out")
    ap.add_argument("--include-settled", action="store_true")
    a = ap.parse_args()
    tm = json.load(open(os.path.join(a.run_dir, "task_map.json"), encoding="utf-8"))
    rid = tm.get("_run")
    if not rid:
        sys.exit("task_map.json has no _run (create the plan first)")
    rev = {v: k for k, v in tm.items() if k != "_run"}
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    workers = []
    for w in herdr_rt.worker_list(a.run_dir)["result"]["workers"]:
        outcome, status = w.get("outcome"), w.get("agentStatus")
        if not a.include_settled and (w.get("released") or outcome == "succeeded"):
            continue
        state = "failed" if outcome == "failed" else ("done" if outcome == "succeeded" else "running")
        row = {"id": w["dispatchId"], "task": rev.get(w["taskId"], w["taskId"]), "state": state}
        if status in LIVE:
            row["last_heartbeat"] = now
        row["liveness"] = "live" if status in LIVE else "unverifiable"
        row["agent_status"] = status
        workers.append(row)
    if not a.include_settled:  # a failed dispatch already replaced by a retry of the same task is history, not an alert
        alive = {w["task"] for w in workers if w["state"] != "failed"}
        workers = [w for w in workers if not (w["state"] == "failed" and w["task"] in alive)]
    snap = {"now": now, "herdr_run": rid, "workers": workers}
    text = json.dumps(snap, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        print(f"wrote {a.out}: {len(workers)} active worker(s)")
    else:
        print(text)


if __name__ == "__main__":
    main()
