#!/usr/bin/env python3
"""Agent roster gate: detect which agent runtimes are usable through Orca, let the human pick, record the choice.

  agent_roster.py detect [--json]
      Reads Orca (status, accounts, hosts) and probes the CLI of every agent id Orca can supervise
      (plus agents Orca cannot supervise, flagged). Read-only; never installs, logs in, or launches an agent session.

  agent_roster.py select <run_dir> --orchestrator ID --code ID[,ID..] --debate ID[,ID..] [--analysis ID[,ID..]]
      Validates the choice against `detect` and writes <run_dir>/agents.json.

  agent_roster.py show <run_dir>

Groups: code = module-dev / integrator / error-analyst / feasibility-analyst (write and run code);
        debate = model-proposer / critic / architect (model selection debate: use >=2 different agents);
        analysis = data-analyst / requirements-analyst / researcher / status-assessor (default: code group).
The orchestrator is the agent session that runs the workflow (it cannot be swapped mid-session; if it differs
from the current session, restart the workflow from that agent). plan_to_orca.py refuses to start workers
without agents.json and only uses agents selected here.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
ORCA = os.environ.get("ORCA_CLI_COMMAND") or ("orca-dev" if os.environ.get("ORCA_DEV_REPO_ROOT") else "orca")

# Orca agent id -> executables to probe. ids from `orca orchestration worker-start --help`.
SUPERVISED = {
    "claude": ["claude"], "codex": ["codex"], "opencode": ["opencode"], "opencode2": ["opencode"],
    "cursor": ["cursor-agent", "agent"], "antigravity": ["antigravity"], "muse": ["muse"], "zcode": ["zcode"],
}
UNSUPERVISED = {"commandcode": ["commandcode"]}  # works only as a manually driven terminal (no worker_done)
GROUPS = ("code", "debate", "analysis")


def run(argv, timeout=15):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def orca_json(*args):
    rc, out = run([ORCA, *args, "--json"], 30)
    i = out.find("{")
    try:
        return json.loads(out[i:]) if rc == 0 and i >= 0 else None
    except ValueError:
        return None


def detect():
    st = orca_json("status")
    runtime_ok = bool(st and st.get("ok") and (st.get("result", {}).get("runtime", {}) or {}).get("reachable"))
    accounts = orca_json("account", "list")
    hosts = orca_json("host", "list")
    rows = []
    for table, supervised in ((SUPERVISED, True), (UNSUPERVISED, False)):
        for aid, exes in table.items():
            path = next((shutil.which(x) for x in exes if shutil.which(x)), None)
            ver = None
            if path:
                rc, out = run([path, "--version"], 8)
                ver = out.strip().splitlines()[0][:60] if rc == 0 and out.strip() else None
            rows.append({"id": aid, "cli": path, "version": ver, "installed": bool(path),
                         "orca_supervised": supervised,
                         "usable": bool(path) and supervised and runtime_ok})
    return {"orca_runtime_reachable": runtime_ok, "orca_accounts": accounts.get("result") if accounts else None,
            "hosts": hosts.get("result") if hosts else None, "agents": rows,
            "note": "CLI present on PATH = installed here; login/credits are not verifiable without launching a session. "
                    "opencode2 shares the opencode CLI. An agent that fails to start is reported by Orca at worker-start."}


def cmd_detect(a):
    d = detect()
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    print(f"Orca runtime: {'OK' if d['orca_runtime_reachable'] else 'NOT reachable (run: orca open)'}")
    print(f"{'agent id':<12} {'installed':<10} {'orca-supervised':<16} {'usable':<7} version / path")
    for r in d["agents"]:
        print(f"{r['id']:<12} {str(r['installed']):<10} {str(r['orca_supervised']):<16} {str(r['usable']):<7} "
              f"{r['version'] or ''} {r['cli'] or ''}")
    print("\n" + d["note"])
    return 0


def split(v):
    return [x for x in (v or "").split(",") if x]


def cmd_select(a):
    d = detect()
    usable = {r["id"] for r in d["agents"] if r["usable"]}
    chosen = {"code": split(a.code), "debate": split(a.debate), "analysis": split(a.analysis) or split(a.code)}
    bad = sorted({x for g in chosen.values() for x in g if x not in usable} | ({a.orchestrator} - usable - {"claude", "codex"}))
    if bad:
        sys.exit(f"not usable through Orca here: {', '.join(bad)} (usable: {', '.join(sorted(usable)) or 'none'})")
    if not chosen["code"] or not chosen["debate"]:
        sys.exit("--code and --debate need at least one agent each")
    warn = []
    if len(set(chosen["debate"])) < 2:
        warn.append("debate group has a single agent: proposals will not be independent (use >=2 different agents)")
    os.makedirs(a.run_dir, exist_ok=True)
    rec = {"orchestrator": a.orchestrator, "groups": chosen, "selected_at": dt.datetime.now().isoformat(timespec="seconds"),
           "usable_at_selection": sorted(usable), "warnings": warn}
    p = os.path.join(a.run_dir, "agents.json")
    json.dump(rec, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("wrote", p, json.dumps(chosen, ensure_ascii=False), "orchestrator:", a.orchestrator)
    for w in warn:
        print("WARNING:", w)
    return 0


def cmd_show(a):
    p = os.path.join(a.run_dir, "agents.json")
    print(open(p, encoding="utf-8").read() if os.path.exists(p) else "no agents.json: run `agent_roster.py detect` then `select`")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    d = sp.add_parser("detect")
    d.add_argument("--json", action="store_true")
    s = sp.add_parser("select")
    s.add_argument("run_dir")
    s.add_argument("--orchestrator", required=True)
    s.add_argument("--code", required=True)
    s.add_argument("--debate", required=True)
    s.add_argument("--analysis")
    sh = sp.add_parser("show")
    sh.add_argument("run_dir")
    a = ap.parse_args()
    sys.exit({"detect": cmd_detect, "select": cmd_select, "show": cmd_show}[a.cmd](a))


if __name__ == "__main__":
    main()
