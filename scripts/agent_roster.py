#!/usr/bin/env python3
"""Agent roster gate: detect which agent runtimes Orca can use, let the human pick, record the choice.

  agent_roster.py detect [--json] [--all]
      Reads Orca's OWN agent catalog (ids + launch commands, extracted from the installed Orca bundle; falls back to a
      built-in list) and probes each launch command on PATH, plus Orca status/accounts/hosts. Read-only: never installs,
      logs in, or launches an agent session. `--all` also lists catalog agents that are not installed.

  agent_roster.py probe ID [--restore-run RUN_ID] [--timeout 150]
      Readiness test of ONE agent through Orca: starts a throwaway Run and a no-op worker (no file changes, a few tokens),
      waits for worker_done, releases it, and rebinds the coordinator to RUN_ID (default: the run bound now).
      Use it before selecting an agent whose login/model setup you are unsure about (an installed CLI can still fail to start).

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
import re
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
ORCA = os.environ.get("ORCA_CLI_COMMAND") or ("orca-dev" if os.environ.get("ORCA_DEV_REPO_ROOT") else "orca")

# fallback when Orca's bundle cannot be read (id -> launch command)
FALLBACK = {"claude": "claude", "codex": "codex", "opencode": "opencode", "opencode2": "opencode2", "cursor": "cursor-agent",
            "antigravity": "agy", "muse": "muse", "zcode": "zcode", "pi": "pi", "kimi": "kimi", "command-code": "command-code",
            "gemini": "gemini", "droid": "droid", "amp": "amp", "grok": "grok", "copilot": "copilot", "hermes": "hermes",
            "devin": "devin", "qoder": "qodercli", "codebuddy": "codebuddy", "aider": "aider", "goose": "goose"}
CAT_RE = re.compile(r"\{id:`([a-z0-9\-]+)`,label:(?:[^{}`]|`[^`]*`)*?cmd:`([^`]+)`")


def run(argv, timeout=15):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def orca_json(*args, timeout=30):
    rc, out = run([ORCA, *args, "--json"], timeout)
    i = out.find("{")
    try:
        return json.loads(out[i:]) if i >= 0 else None
    except ValueError:
        return None


def orca_catalog():
    """(catalog dict id->cmd, source). Orca's agent catalog lives in its app bundle; the CLI has no 'list agents'."""
    exe = shutil.which(ORCA)
    if exe:
        asar = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(exe))), "app.asar")
        if os.path.exists(asar):
            try:
                txt = open(asar, "rb").read().decode("utf-8", "replace")
                cat = {}
                for m in CAT_RE.finditer(txt):
                    cat.setdefault(m.group(1), m.group(2))
                if len(cat) >= 10:
                    return cat, "orca bundle"
            except OSError:
                pass
    return dict(FALLBACK), "built-in fallback"


def extra_dirs():
    """Dirs where an agent CLI may live although it is not on this process's PATH: registry PATH (user+machine)
    and every nvm Node version dir (a CLI installed under another Node version is invisible once nvm switches)."""
    dirs = []
    if os.name == "nt":
        rc, out = run(["powershell", "-NoProfile", "-Command",
                       "[Environment]::GetEnvironmentVariable('Path','User')+';'+[Environment]::GetEnvironmentVariable('Path','Machine')"], 15)
        dirs += [d for d in out.strip().split(";") if d]
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "nvm")
        if os.path.isdir(base):
            dirs += [os.path.join(base, d) for d in sorted(os.listdir(base)) if d.startswith("v")]
    else:
        nvm = os.path.expanduser("~/.nvm/versions/node")
        if os.path.isdir(nvm):
            dirs += [os.path.join(nvm, d, "bin") for d in sorted(os.listdir(nvm))]
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


def find_cli(cmd, extra):
    """(path, on_active_path). on_active_path False = installed elsewhere; a shell/Orca launch of `cmd` may fail."""
    p = shutil.which(cmd)
    if p:
        return p, True
    p = shutil.which(cmd, path=os.pathsep.join(extra))
    return p, False


def detect(show_all=False):
    st = orca_json("status")
    runtime_ok = bool(st and st.get("ok") and (st.get("result", {}).get("runtime", {}) or {}).get("reachable"))
    accounts, hosts = orca_json("account", "list"), orca_json("host", "list")
    cat, src = orca_catalog()
    rows = []
    extra = extra_dirs()
    for aid, cmd in sorted(cat.items()):
        path, active = find_cli(cmd, extra)
        if not path and not show_all:
            continue
        ver = None
        if path:
            rc, out = run([path, "--version"], 8)
            ver = out.strip().splitlines()[0][:60] if rc == 0 and out.strip() else None
        rows.append({"id": aid, "cmd": cmd, "cli": path, "version": ver, "installed": bool(path),
                     "on_active_path": active if path else None, "usable": bool(path) and runtime_ok})
    return {"orca_runtime_reachable": runtime_ok, "catalog_source": src, "catalog_size": len(cat),
            "orca_accounts": accounts.get("result") if accounts else None, "hosts": hosts.get("result") if hosts else None,
            "agents": rows,
            "note": "usable = launch command found on PATH and the Orca runtime is reachable. Login/model setup is NOT verified: "
                    "run `agent_roster.py probe <id>` (a no-op worker) before relying on an agent."}


def cmd_detect(a):
    d = detect(a.all)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    print(f"Orca runtime: {'OK' if d['orca_runtime_reachable'] else 'NOT reachable (run: orca open)'}  | catalog: {d['catalog_size']} agents ({d['catalog_source']})")
    print(f"{'agent id':<13} {'installed':<10} {'usable':<7} command / version")
    for r in d["agents"]:
        warn = "  [NOT on the active PATH: installed under another Node version/dir; launch may fail, see probe]" if r["installed"] and not r["on_active_path"] else ""
        print(f"{r['id']:<13} {str(r['installed']):<10} {str(r['usable']):<7} {r['cmd']}  {r['version'] or ''}{warn}")
    print("\n" + d["note"])
    return 0


def current_run():
    d = orca_json("orchestration", "run-current")
    try:
        return d["result"]["run"]["id"]
    except (TypeError, KeyError):
        return None


def cmd_probe(a):
    cat, _ = orca_catalog()
    if a.id not in cat:
        sys.exit(f"{a.id} is not in Orca's agent catalog")
    restore = a.restore_run or current_run()
    r = orca_json("orchestration", "run-create", "--objective", f"probe agent {a.id} (no file changes)")
    pr = r["result"]["run"]["id"]
    ok, detail = False, ""
    try:
        s = orca_json("orchestration", "worker-start", "--run", pr, "--spec",
                      "TASK PROBE: do not read or modify any file. Just finish: send worker_done with outcome succeeded and the one-sentence summary 'probe ok'.",
                      "--task-title", f"probe {a.id}", "--worktree", "current", "--agent", a.id, timeout=int(a.timeout) + 60)
        res = (s or {}).get("result", {})
        if not s or not s.get("ok") or res.get("state") not in ("ready", "running"):
            detail = f"start failed at stage '{res.get('stage') or res.get('failedStage') or (s or {}).get('error', {}).get('code')}'"
        else:
            deadline = time.time() + a.timeout
            while time.time() < deadline:
                c = orca_json("orchestration", "check", "--run", pr, "--wait", "--types", "worker_done,escalation,question",
                              "--timeout-ms", "20000", timeout=60)
                msgs = (c or {}).get("result", {}).get("messages", [])
                if any(m["type"] == "worker_done" for m in msgs):
                    ok = True
                    break
                if any(m["type"] in ("escalation", "question") for m in msgs):
                    detail = "agent asked a question/escalated (needs interactive setup)"
                    break
            else:
                detail = f"no worker_done within {a.timeout}s"
        wl = orca_json("orchestration", "worker-list", "--run", pr)
        for w in ((wl or {}).get("result", {}).get("workers") or (wl or {}).get("result", {}).get("rows") or []):
            if w.get("dispatchId"):
                if not ok:  # show what the agent's terminal looked like: usually a trust/login/permission prompt
                    rd = orca_json("orchestration", "worker-read", "--dispatch", w["dispatchId"], "--source", "auto")
                    txt = json.dumps((rd or {}).get("result", {}), ensure_ascii=False)
                    detail += " | last output: " + re.sub(r"\n|\s+", " ", txt)[-500:]
                orca_json("orchestration", "worker-release", "--dispatch", w["dispatchId"])
    finally:
        if restore:
            orca_json("orchestration", "run-use", "--id", restore)
    print(f"probe {a.id}: {'READY (worker_done received)' if ok else 'NOT READY — ' + detail}; coordinator rebound to {restore}")
    return 0 if ok else 1


def split(v):
    return [x for x in (v or "").split(",") if x]


def cmd_select(a):
    d = detect()
    usable = {r["id"] for r in d["agents"] if r["usable"]}
    chosen = {"code": split(a.code), "debate": split(a.debate), "analysis": split(a.analysis) or split(a.code)}
    bad = sorted({x for g in chosen.values() for x in g if x not in usable} | ({a.orchestrator} - usable))
    if bad:
        sys.exit(f"not usable here: {', '.join(bad)} (usable: {', '.join(sorted(usable)) or 'none'}). "
                 "Not installed or Orca runtime down: see `agent_roster.py detect --all`.")
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
    d.add_argument("--all", action="store_true")
    pr = sp.add_parser("probe")
    pr.add_argument("id")
    pr.add_argument("--restore-run")
    pr.add_argument("--timeout", type=int, default=150)
    s = sp.add_parser("select")
    s.add_argument("run_dir")
    s.add_argument("--orchestrator", required=True)
    s.add_argument("--code", required=True)
    s.add_argument("--debate", required=True)
    s.add_argument("--analysis")
    sh = sp.add_parser("show")
    sh.add_argument("run_dir")
    a = ap.parse_args()
    sys.exit({"detect": cmd_detect, "probe": cmd_probe, "select": cmd_select, "show": cmd_show}[a.cmd](a))


if __name__ == "__main__":
    main()
