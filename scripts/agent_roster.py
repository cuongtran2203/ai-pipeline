#!/usr/bin/env python3
"""Agent roster gate: detect which agent CLIs Herdr can host, let the human pick, record the choice.

  agent_roster.py detect [--json] [--all]
      Probes each known agent launch command on PATH (Herdr auto-detects an agent from the command running in a pane,
      so its catalog = the built-in list below) and checks the herdr CLI/server. Read-only: never installs, logs in, or
      launches an agent session. `--all` also lists agents that are not installed.

  agent_roster.py probe ID [--timeout 150] [--long] [--model ID]
      Readiness test of ONE agent through Herdr: creates a throwaway run dir + tab/pane, starts the agent with a no-op task
      (no file changes, a few tokens), waits for worker_done, closes the pane. Use it before selecting an agent whose
      login/model setup you are unsure about (an installed CLI can still fail to start).

  agent_roster.py select <run_dir> --orchestrator ID --code ID[,ID..] --debate ID[,ID..] [--analysis ID[,ID..]]
      Validates the choice against `detect` and writes <run_dir>/agents.json.

  agent_roster.py show <run_dir>

Groups: code = module-dev / integrator / error-analyst / feasibility-analyst (write and run code);
        debate = model-proposer / critic / architect (model selection debate: use >=2 different agents);
        analysis = data-analyst / requirements-analyst / researcher / status-assessor (default: code group).
The orchestrator is the agent session that runs the workflow (it cannot be swapped mid-session; if it differs
from the current session, restart the workflow from that agent). plan_to_herdr.py refuses to start workers
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import herdr_rt  # noqa: E402

# known agents (id -> launch command); Herdr recognises them by the command running in a pane
CATALOG = {"claude": "claude", "codex": "codex", "opencode": "opencode", "opencode2": "opencode2", "cursor": "cursor-agent",
            "antigravity": "agy", "muse": "muse", "zcode": "zcode", "pi": "pi", "kimi": "kimi", "command-code": "command-code",
            "gemini": "gemini", "droid": "droid", "amp": "amp", "grok": "grok", "copilot": "copilot", "hermes": "hermes",
            "devin": "devin", "qoder": "qodercli", "codebuddy": "codebuddy", "aider": "aider", "goose": "goose"}


def run(argv, timeout=15):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def catalog():
    return dict(CATALOG), "built-in list"


def herdr_ready():
    """(installed, server_reachable). Server may be stopped: `herdr` launches/attaches the default session."""
    if shutil.which(herdr_rt.HERDR) is None:
        return False, False
    rc, _ = run([herdr_rt.HERDR, "session", "list"], 10)
    return True, rc == 0


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
    """(path, on_active_path). on_active_path False = installed elsewhere; a pane launch of `cmd` may fail."""
    p = shutil.which(cmd)
    if p:
        return p, True
    p = shutil.which(cmd, path=os.pathsep.join(extra))
    return p, False


def detect(show_all=False):
    installed, runtime_ok = herdr_ready()
    cat, src = catalog()
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
                     "on_active_path": active if path else None, "usable": bool(path) and installed})
    return {"herdr_installed": installed, "herdr_runtime_reachable": runtime_ok, "catalog_source": src, "catalog_size": len(cat),
            "agents": rows,
            "note": "usable = launch command found on PATH and the herdr CLI is installed. Login/model setup is NOT verified: "
                    "run `agent_roster.py probe <id>` (a no-op worker) before relying on an agent."}


def cmd_detect(a):
    d = detect(a.all)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    print(f"Herdr: {'installed' if d['herdr_installed'] else 'NOT installed (https://github.com/herdrdev/herdr)'}, session {'reachable' if d['herdr_runtime_reachable'] else 'not running (run: herdr)'}  | catalog: {d['catalog_size']} agents ({d['catalog_source']})")
    print(f"{'agent id':<13} {'installed':<10} {'usable':<7} command / version")
    for r in d["agents"]:
        warn = "  [NOT on the active PATH: installed under another Node version/dir; launch may fail, see probe]" if r["installed"] and not r["on_active_path"] else ""
        print(f"{r['id']:<13} {str(r['installed']):<10} {str(r['usable']):<7} {r['cmd']}  {r['version'] or ''}{warn}")
    print("\n" + d["note"])
    return 0


def cmd_probe(a):
    import glob
    import tempfile
    cat, _ = catalog()
    if a.id not in cat:
        sys.exit(f"{a.id} is not in the agent list")
    herdr_rt.require_herdr()
    rd = tempfile.mkdtemp(prefix=f"probe-{a.id}-")
    r = herdr_rt.run_create(rd, f"probe agent {a.id} (no file changes)")["result"]["id"]
    spec = "TASK PROBE: do not read or modify any file. Just finish."
    if a.long:  # realistic task-spec size/shape: several KB, many lines, quotes, backticks, non-ASCII
        nl = chr(10)
        filler = nl.join(f"Constraint {k}: keep `code` intact, quote 'text', use Vietnamese diacritics (ă â ê ô ơ ư đ), paths like C:/work/run-{k}/file.md; padding for the probe only." for k in range(1, 36))
        spec = spec + nl + nl + "Padding that mimics a real task spec (ignore it):" + nl + filler
    tid = herdr_rt.task_create(rd, r, f"probe {a.id}", spec, [])["result"]["id"]
    s = herdr_rt.worker_start(rd, r, tid, "current", f"probe-{a.id}", a.id, a.model, None)
    ok, detail = False, ""
    if not s.get("ok"):
        detail = "start failed: " + json.dumps(s.get("result") or s, ensure_ascii=False)[:300]
    else:
        deadline = time.time() + a.timeout
        while time.time() < deadline:
            ev = herdr_rt.check(rd, True, 20000, {"worker_done", "escalation", "question"})["result"]["events"]
            if any(e["type"] == "worker_done" for e in ev):
                ok = True
                break
            if ev:
                detail = "agent asked a question/escalated (needs interactive setup)"
                break
        else:
            pane = s["result"]["pane"]
            _, out, _ = herdr_rt.herdr(*herdr_rt.HERDR_CMDS["pane_read"], pane, "--source", "recent")
            detail = f"no worker_done within {a.timeout}s | last output: " + re.sub(r"\s+", " ", out)[-500:]
        herdr_rt.worker_release(rd, s["result"]["dispatchId"])
    print(f"probe {a.id}: {'READY (worker_done received)' if ok else 'NOT READY — ' + detail}")
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
                 "Not installed or herdr missing: see `agent_roster.py detect --all`.")
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
    pr.add_argument("--timeout", type=int, default=150)
    pr.add_argument("--model", help="provider model id to test together with the agent (e.g. gpt-6-sol for codex)")
    pr.add_argument("--long", action="store_true", help="use a realistic multi-KB task spec (catches agents that drop long prompts)")
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
