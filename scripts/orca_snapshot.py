#!/usr/bin/env python3
"""Read-only bridge: Orca worker status -> workers.json for scripts/supervisor.py.

Usage: orca_snapshot.py <run_dir> [--out FILE] [--include-settled]
       python scripts/orca_snapshot.py runs/<id> --out runs/<id>/workers.json
       python scripts/supervisor.py runs/<id> --workers runs/<id>/workers.json

Calls only `orca orchestration worker-list --run <orca_run_id> --json` (read-only; the Orca run id comes from
<run_dir>/task_map.json `_run`). Per worker: id = dispatchId, task = plan task id (via task_map.json), state running|failed|done,
last_heartbeat = Orca's last agent-status observation (ISO UTC) when the agent is live; missing when Orca cannot observe it
(liveness `unverifiable`), which the supervisor reports instead of guessing. Settled/released workers are skipped unless
--include-settled. Never starts, stops, abandons or releases anything.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
ORCA = os.environ.get("ORCA_CLI_COMMAND") or ("orca-dev" if os.environ.get("ORCA_DEV_REPO_ROOT") else "orca")


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
    r = subprocess.run([ORCA, "orchestration", "worker-list", "--run", rid, "--json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    t = r.stdout
    try:
        d = json.loads(t[t.find("{"):])["result"]
    except (ValueError, KeyError):
        sys.exit(f"cannot read worker-list for {rid}: {(r.stderr or t)[:200]}")
    workers = []
    for w in d.get("workers") or d.get("rows") or []:
        p = w.get("projection", {})
        outcome = p.get("outcome")
        term = w.get("terminalState")
        if not a.include_settled and (term == "released" or outcome in ("succeeded",)):
            continue
        state = "failed" if outcome in ("failed", "abandoned") or w.get("workerState") in ("failed", "abandoned") else (
            "done" if outcome == "succeeded" else "running")
        liv = p.get("liveness") or {}
        hb = None
        if liv.get("verdict") == "live" and liv.get("observedAt"):
            hb = dt.datetime.fromtimestamp(liv["observedAt"] / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = {"id": w.get("dispatchId"), "task": rev.get(w.get("taskId"), w.get("taskId")), "state": state}
        if hb:
            row["last_heartbeat"] = hb
        row["liveness"] = liv.get("verdict")
        workers.append(row)
    if not a.include_settled:  # a failed/abandoned dispatch already replaced by a retry of the same task is history, not an alert
        alive = {w["task"] for w in workers if w["state"] != "failed"}
        workers = [w for w in workers if not (w["state"] == "failed" and w["task"] in alive)]
    snap = {"now": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "orca_run": rid, "workers": workers}
    text = json.dumps(snap, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        print(f"wrote {a.out}: {len(workers)} active worker(s)")
    else:
        print(text)


if __name__ == "__main__":
    main()
