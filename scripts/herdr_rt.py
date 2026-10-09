#!/usr/bin/env python3
"""Herdr runtime backend (thay Orca tu 1.0.0.rc; ban Orca: tag 1.0.0). Stdlib only.

Herdr (https://github.com/herdrdev/herdr) la terminal multiplexer cho coding agent: workspace/tab/pane,
trang thai agent working|blocked|idle|done, `pane run|send-text|read`, `agent list|get|wait`. Herdr KHONG co
task DAG, run, worker_done, ask. Phan dieu phoi do file-state trong run dir:

  <run_dir>/herdr_run.json          run id + workspace
  <run_dir>/tasks/<task_id>.json    spec/title/deps (thay `orca task-create`)
  <run_dir>/workers/<dispatch>.json worker da start (task, pane, worktree, branch, agent)
  <run_dir>/worker_done/<TASK>.json worker ghi bang scripts/worker_done.py done (thay worker_done cua Orca)
  <run_dir>/asks/<TASK>-<n>.json    worker hoi nguoi (scripts/worker_done.py ask); pane se o trang thai blocked

Moi lenh herdr that di qua `herdr()` / bien HERDR (HERDR_CLI_COMMAND de ghi de). Cu phap co dinh o mot cho
(HERDR_CMDS) de sua khi doi chieu voi `herdr <cmd> --help`: chay `python scripts/herdr_rt.py verify`.

CLI: herdr_rt.py <op> [--run-dir D] ...   op = run-create|task-create|worker-start|check|worker-list|worker-release|verify
Moi op in ra JSON dang {"ok": true, "result": {...}} (cung dang receipt cu de plan_to_herdr khong doi logic admission).
"""
import argparse
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERDR = os.environ.get("HERDR_CLI_COMMAND") or "herdr"

# Cu phap herdr (theo https://herdr.dev/docs/cli-reference/). Sua DUY NHAT o day neu `--help` khac.
HERDR_CMDS = {
    "workspace_create": ["workspace", "create"],        # + --label N --cwd D --no-focus
    "tab_create": ["tab", "create"],                    # + --workspace W --label N --cwd D --env K=V --no-focus  (1 tab/pane rieng/worker)
    "pane_list": ["pane", "list"],
    "pane_run": ["pane", "run"],                        # + <pane> <command>
    "pane_send_text": ["pane", "send-text"],            # + <pane> <text>
    "pane_read": ["pane", "read"],                      # + <pane> [--source recent]
    "pane_close": ["pane", "close"],                    # + <pane>
    "agent_start": ["agent", "start"],                  # + <name> --kind K --pane P [-- args]
    "agent_prompt": ["agent", "prompt"],                # + <name> <text>
    "agent_get": ["agent", "get"],                      # + <target>
    "agent_wait": ["agent", "wait"],                    # + <target> [--status ..] [--timeout ..]
}
# Lenh agent -> cach khoi dong trong pane (herdr tu nhan dien agent theo ten lenh).
AGENT_KINDS = {"pi", "claude", "codex", "gemini", "cursor", "devin", "agy", "cline", "omp", "mastracode", "opencode",
               "copilot", "kimi", "kiro", "droid", "amp", "grok", "hermes", "kilo", "qodercli", "qwen", "letta", "maki", "muse"}
AGENT_CMD = {"claude": "claude", "codex": "codex", "pi": "pi", "opencode": "opencode", "cursor": "cursor-agent",
             "gemini": "gemini", "kimi": "kimi", "qwen": "qwen"}


def py():
    """Lenh Python cho prompt worker: macOS/Linux thuong chi co python3."""
    return "python" if shutil.which("python") else "python3"


def q(path):
    """Quote duong dan cho shell cua pane (POSIX hoac PowerShell/cmd)."""
    return f'"{path}"' if os.name == "nt" else shlex.quote(path)


def require_herdr():
    if shutil.which(HERDR) is None:
        sys.exit(f"plan error: herdr CLI '{HERDR}' not found on PATH (cai: https://github.com/herdrdev/herdr; "
                 "HERDR_CLI_COMMAND de ghi de)")
    return HERDR


def herdr(*args, timeout=60, check=False):
    """Chay 1 lenh herdr that. Tra (rc, stdout, stderr). Khong bao gio sys.exit tru khi check=True."""
    try:
        r = subprocess.run([HERDR, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            sys.exit(f"herdr {' '.join(args)}: {e}")
        return 127, "", str(e)
    if check and r.returncode != 0:
        sys.exit(f"herdr {' '.join(args)} failed: {r.stdout}\n{r.stderr}")
    return r.returncode, r.stdout, r.stderr


def _json(text):
    t = text.strip()
    for i, ch in enumerate(t):
        if ch in "{[":
            try:
                return json.loads(t[i:])
            except ValueError:
                return None
    return None


def find_id(obj, keys=("pane_id", "id")):
    """Id dau tien trong JSON (herdr in JSON cho hau het lenh)."""
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), str):
                return obj[k]
        for v in obj.values():
            r = find_id(v, keys)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_id(v, keys)
            if r:
                return r
    return None


def _rd(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _wr(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def ok(**result):
    return {"ok": True, "result": result}


def err(msg, **extra):
    return {"ok": False, "error": msg, **extra}


# ---------------------------------------------------------------- ops

def run_create(run_dir, objective):
    prior = _rd(os.path.join(run_dir, "herdr_run.json"))
    if prior and prior.get("id"):
        return ok(**prior)  # idempotent: 1 Run / run dir
    rid = "run-" + uuid.uuid4().hex[:8]
    ws = None
    rc, out, _ = herdr(*HERDR_CMDS["workspace_create"], "--label", rid, "--cwd", ROOT, "--no-focus")
    if rc == 0:
        ws = find_id(_json(out), ("workspace_id",))
    rec = {"id": rid, "objective": objective, "workspace": ws, "created": int(time.time())}
    _wr(os.path.join(run_dir, "herdr_run.json"), rec)
    return ok(**rec)


def task_create(run_dir, run_id, title, spec, deps):
    tid = "task-" + uuid.uuid4().hex[:8]
    _wr(os.path.join(run_dir, "tasks", tid + ".json"),
        {"id": tid, "run": run_id, "title": title, "spec": spec, "deps": deps})
    return ok(id=tid)


def _git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _slug(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "w"


def worker_start(run_dir, run_id, task_id, worktree, name, agent, model, effort):
    task = _rd(os.path.join(run_dir, "tasks", task_id + ".json"))
    if not task:
        return err(f"task {task_id} khong co trong {run_dir}/tasks (chay --create truoc)")
    disp = "disp-" + uuid.uuid4().hex[:8]
    cwd, branch = ROOT, None
    if worktree in ("new-child", "new-top-level"):
        branch = _slug(name or disp)
        cwd = os.path.join(os.path.dirname(ROOT), ".worktrees", os.path.basename(ROOT), branch)
        r = _git("worktree", "add", "-b", branch, cwd)
        if r.returncode:
            return {"ok": False, "result": {"failedStage": "worktree", "detail": (r.stderr or r.stdout).strip()[:300]}}
    pdir = os.path.join(run_dir, "workers")
    prompt = os.path.join(pdir, disp + ".prompt.md")
    os.makedirs(pdir, exist_ok=True)
    done_cmd = f"{py()} {q(os.path.join(ROOT, 'scripts', 'worker_done.py'))} {q(os.path.abspath(run_dir))}"
    footer = (f"\n\n[Herdr protocol] Task id: {task['id']}. Khi xong chay: {done_cmd} done --task {task_id} "
              f"--outcome succeeded|failed --report-path <output chinh> --summary '<1-3 cau>'. "
              f"Can quyet dinh cua nguoi: {done_cmd} ask --task {task_id} --question '<cau hoi>' roi DUNG va doi tra loi trong pane "
              "(KHONG doan).")
    with open(prompt, "w", encoding="utf-8") as f:
        f.write(task["spec"] + footer)
    cmd = AGENT_CMD.get(agent, agent)
    if model and agent == "claude":
        cmd += f" --model {model}"
    ws = (_rd(os.path.join(run_dir, "herdr_run.json")) or {}).get("workspace")
    rc, out, _ = herdr(*HERDR_CMDS["tab_create"], *(["--workspace", ws] if ws else []), "--label", _slug(name or disp),
                       "--cwd", cwd, "--env", f"HERDR_DISPATCH_ID={disp}", "--no-focus")
    pane = find_id(_json(out), ("pane_id", "root_pane_id")) if rc == 0 else None
    if not pane:
        return {"ok": False, "result": {"failedStage": "pane", "detail": f"tab create khong tra pane ({out[:200]})"}}
    msg = f"Doc {prompt} va thuc hien dung task do. Day la task cua ban."
    if agent in AGENT_KINDS:  # agent duoc herdr ho tro: start doi san sang that, roi prompt
        extra = ["--", "--model", model] if (model and agent == "claude") else []
        steps = [HERDR_CMDS["agent_start"] + [disp, "--kind", agent, "--pane", pane, "--timeout", "120000"] + extra,
                 HERDR_CMDS["agent_prompt"] + [disp, msg]]
    else:  # agent herdr khong phan loai (vd command-code): chay lenh trong pane, gui prompt bang send-text + Enter
        steps = [HERDR_CMDS["pane_run"] + [pane, cmd],
                 HERDR_CMDS["pane_send_text"] + [pane, msg],
                 ["pane", "send-keys", pane, "Enter"]]
    for st in steps:
        rc, out, e = herdr(*st, timeout=150)
        if rc != 0:
            return {"ok": False, "result": {"failedStage": "launch", "detail": (e or out)[:300], "residualResources": [pane]}}
        if st is steps[0] and agent not in AGENT_KINDS:
            time.sleep(3)
    _wr(os.path.join(pdir, disp + ".json"),
        {"dispatchId": disp, "taskId": task_id, "run": run_id, "pane": pane, "cwd": cwd, "branch": branch,
         "agent": agent, "started": int(time.time())})
    return ok(dispatchId=disp, pane=pane, branch=branch, worktree=cwd)


def _workers(run_dir):
    return [w for w in (_rd(p) for p in sorted(glob.glob(os.path.join(run_dir, "workers", "disp-*.json")))) if w]


def _done_files(run_dir):
    return {os.path.basename(p)[:-5]: _rd(p, {}) for p in glob.glob(os.path.join(run_dir, "worker_done", "*.json"))}


def check(run_dir, wait, timeout_ms, types):
    """Cho event: worker_done | escalation (failed) | question (ask chua tra loi). Tra ngay neu da co."""
    deadline = time.time() + timeout_ms / 1000.0
    seen = set()
    while True:
        events = []
        disp_of = {w["taskId"]: w["dispatchId"] for w in _workers(run_dir)}
        for k, d in _done_files(run_dir).items():
            kind = "worker_done" if d.get("outcome") == "succeeded" else "escalation"
            if kind in types:
                events.append({"type": kind, "task": k, "outcome": d.get("outcome"), "reportPath": d.get("report_path"),
                               "summary": d.get("summary"), "worker": disp_of.get(k)})
        for p in glob.glob(os.path.join(run_dir, "asks", "*.json")):
            a = _rd(p, {})
            if "question" in types and a.get("question") and not a.get("answer"):
                events.append({"type": "question", "ask": os.path.basename(p)[:-5], "question": a["question"]})
        fresh = [e for e in events if json.dumps(e, sort_keys=True) not in seen]
        if fresh or not wait or time.time() >= deadline:
            return ok(events=fresh, timedOut=(not fresh and wait))
        time.sleep(2)


def worker_list(run_dir):
    done = _done_files(run_dir)
    rows = []
    for w in _workers(run_dir):
        d = done.get(w["taskId"]) or {}
        state = None
        rc, out, _ = herdr(*HERDR_CMDS["agent_get"], w["pane"])
        if rc == 0 and (j := _json(out)):
            state = (j.get("status") if isinstance(j, dict) else None) or find_id(j, ("status",))
        rows.append({"dispatchId": w["dispatchId"], "taskId": w["taskId"], "pane": w["pane"], "branch": w.get("branch"),
                     "agentStatus": state, "outcome": d.get("outcome"), "released": bool(w.get("released"))})
    return ok(workers=rows)


def worker_release(run_dir, dispatch):
    p = os.path.join(run_dir, "workers", dispatch + ".json")
    w = _rd(p)
    if not w:
        return err(f"dispatch {dispatch} khong ton tai")
    herdr(*HERDR_CMDS["pane_close"], w["pane"])
    w["released"] = True
    _wr(p, w)
    return ok(dispatchId=dispatch, released=True)  # worktree/branch do branch_cleanup.py xu ly sau review


def verify():
    """Doi chieu cu phap voi `herdr ... --help` (khong sua trang thai)."""
    if shutil.which(HERDR) is None:
        return err(f"herdr '{HERDR}' khong co tren PATH")
    rows = {}
    for name, argv in HERDR_CMDS.items():
        rc, out, e = herdr(*argv, "--help")
        rows[name] = {"argv": argv, "help_ok": rc == 0}
    rc, out, _ = herdr("--version")
    return ok(version=out.strip(), commands=rows, all_ok=all(r["help_ok"] for r in rows.values()))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["run-create", "task-create", "worker-start", "check", "worker-list", "worker-release", "verify"])
    ap.add_argument("--run-dir")
    ap.add_argument("--run")
    ap.add_argument("--objective", default="")
    ap.add_argument("--task-title", default="")
    ap.add_argument("--spec", default="")
    ap.add_argument("--deps", default="[]")
    ap.add_argument("--task")
    ap.add_argument("--worktree", default="current")
    ap.add_argument("--name")
    ap.add_argument("--agent", default="claude")
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--dispatch")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--timeout-ms", type=int, default=900000)
    ap.add_argument("--types", default="worker_done,escalation,question")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.op != "verify" and not a.run_dir:
        sys.exit("--run-dir bat buoc")
    if a.op == "run-create":
        res = run_create(a.run_dir, a.objective)
    elif a.op == "task-create":
        res = task_create(a.run_dir, a.run, a.task_title, a.spec, json.loads(a.deps))
    elif a.op == "worker-start":
        res = worker_start(a.run_dir, a.run, a.task, a.worktree, a.name, a.agent, a.model, a.effort)
    elif a.op == "check":
        res = check(a.run_dir, a.wait, a.timeout_ms, set(a.types.split(",")))
    elif a.op == "worker-list":
        res = worker_list(a.run_dir)
    elif a.op == "worker-release":
        res = worker_release(a.run_dir, a.dispatch)
    else:
        res = verify()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
