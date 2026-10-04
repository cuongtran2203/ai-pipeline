#!/usr/bin/env python3
"""Hook cuong che PreToolUse cho Claude Code (ai-pipeline, wave HK).

Doc JSON su kien tu stdin (giao thuc hook Claude Code, xem
https://code.claude.com/docs/en/hooks.md -- da kiem chung):
    {"session_id":..., "transcript_path":..., "cwd":...,
     "hook_event_name":"PreToolUse", "tool_name":"Bash",
     "tool_input":{"command":"..."}, "tool_use_id":...}
- Chan  -> exit code 2 + thong diep stderr (+ JSON stdout
           hookSpecificOutput.permissionDecision=deny).
- Cho qua -> exit 0, khong output.

6 quy tac (bat/tat trong guard_policy.json, mac dinh BAT):
  R1 label_protection : bao ve nhan test (seal) tru integrator/evaluator.
  R2 host_install     : cam cai goi tren host (cho phep trong container).
  R3 dangerous_docker : cam docker nguy hiem.
  R4 ownership        : chi sua trong duong dan owns cua task.
  R5 bugfix_tests     : task bugfix khong duoc sua tests/ de lam xanh.
  R6 destructive      : cam lenh pha hoai (rm -rf, push --force, reset --hard).

Role CHI lay tu bien moi truong AI_PIPELINE_ROLE hoac file task context
(AI_PIPELINE_TASK_CONTEXT / <run>/task_context.json); truong role trong
JSON su kien hoac tool_input BI BO QUA (chong gia mao).

Policy hong -> fail-closed cho R1/R3 (dung policy mac dinh de chan),
fail-open cho R4/R5 (thieu ownership/task-context -> bo qua). Xem README.

Moi quyet dinh (allow/deny + ly do + rule id) append vao guard_audit.jsonl
qua scripts/statefile.py. Audit hong khong lam hong quyet dinh chan/cho.

Che do thu cong/CI (Codex khong co hook tuong duong):
    echo '<event-json>' | python scripts/pipeline_guard.py --check
    python scripts/pipeline_guard.py --check --tool Bash \
        --input '{"command":"pip install x"}' --cwd .

Stdlib only. Da nen tang Linux/macOS/Windows (pathlib/os.path,
subprocess khong shell, encoding utf-8).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import statefile
except ImportError:  # chay doc lap khi thieu statefile: audit bo qua
    statefile = None

GUARD_VERSION = "hk-v1"
ALLOWED_ROLES = ("integrator", "evaluator")

DEFAULT_POLICY = {
    "version": 1,
    "rules": {
        "label_protection": True,
        "host_install": True,
        "dangerous_docker": True,
        "ownership": True,
        "bugfix_tests": True,
        "destructive": True,
    },
    "protected_paths": [
        "eval_manifest.json",
        "seal_audit.jsonl",
        "recipe_lock.json",
        "labels-sealed",
        ".sealed.json",
        "/labels/",
        "\\labels\\",
        "sealed",
    ],
    "allowed_packages": [],
    "run_prefix": "aipipeline-",
    "main_branches": ["main", "master"],
}

FILE_TOOLS = ("Read", "Edit", "Write")

INSTALL_PATTERNS = [
    r"\bpip3?\s+install\b",
    r"\bpython(?:3(?:\.\d+)?)?\s+-m\s+pip\s+install\b",
    r"\bnpm\s+(?:install|i)\b",
    r"\byarn\s+add\b",
    r"\bapt(?:-get)?\s+install\b",
    r"\bconda\s+install\b",
    r"\bbrew\s+install\b",
    r"\bcargo\s+install\b",
    r"\bgo\s+install\b",
]
CONTAINER_HINTS = [
    r"\bdocker\s+(exec|run)\b",
    r"\bssh\b[^\n|;]*\bdocker\b",
]


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(p):
    """Chuan hoa duong dan de so sanh da nen tang (Windows + POSIX)."""
    return str(p).replace("\\", "/").lower()


def abs_of(path, base):
    p = os.path.expanduser(str(path))
    if not os.path.isabs(p):
        p = os.path.join(base or os.getcwd(), p)
    return os.path.normpath(p)


def _is_under(path, root):
    try:
        return os.path.commonpath(
            [os.path.normcase(os.path.abspath(path)),
             os.path.normcase(os.path.abspath(root))]) == os.path.normcase(os.path.abspath(root))
    except ValueError:
        return False


# --- Tim policy / run dir / audit (uu tien: env > run dir > .ai-pipeline > mac dinh) ---

def _walk_up(start):
    d = os.path.abspath(start or os.getcwd())
    while True:
        yield d
        parent = os.path.dirname(d)
        if parent == d:
            return
        d = parent


def find_policy(cwd):
    env = os.environ.get("AI_PIPELINE_GUARD_POLICY")
    if env and os.path.isfile(env):
        return env
    for d in _walk_up(cwd):
        cand = os.path.join(d, ".ai-pipeline", "guard_policy.json")
        if os.path.isfile(cand):
            return cand
        cand = os.path.join(d, "guard_policy.json")
        if os.path.isfile(cand) and os.path.isfile(os.path.join(d, "spec.md")):
            return cand
    return None


def find_run_dir(cwd):
    env = os.environ.get("AI_PIPELINE_RUN_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    for d in _walk_up(cwd):
        if os.path.isfile(os.path.join(d, "spec.md")) and os.path.isfile(os.path.join(d, "done.json")):
            return d
    return None


def find_audit_path(cwd, run_dir):
    env = os.environ.get("AI_PIPELINE_GUARD_AUDIT")
    if env:
        return env
    if run_dir:
        return os.path.join(run_dir, "guard_audit.jsonl")
    for d in _walk_up(cwd):
        if os.path.isdir(os.path.join(d, ".ai-pipeline")):
            return os.path.join(d, ".ai-pipeline", "guard_audit.jsonl")
    return os.path.join(os.path.abspath(cwd or os.getcwd()), "guard_audit.jsonl")


def load_policy(path):
    """Tra (policy, corrupt: bool). Khong file -> mac dinh (khong corrupt)."""
    if not path:
        return dict(DEFAULT_POLICY), False
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = json.load(f)
        if not isinstance(raw, dict) or not isinstance(raw.get("rules"), dict):
            raise ValueError("guard_policy phai la object co khoa 'rules'")
        merged = dict(DEFAULT_POLICY)
        merged.update(raw)
        rules = dict(DEFAULT_POLICY["rules"])
        rules.update({k: bool(v) for k, v in raw["rules"].items()})
        merged["rules"] = rules
        return merged, False
    except (OSError, ValueError):
        return dict(DEFAULT_POLICY), True


def get_role(run_dir):
    role = (os.environ.get("AI_PIPELINE_ROLE") or "").strip().lower()
    if role:
        return role, "env:AI_PIPELINE_ROLE"
    ctx_path = os.environ.get("AI_PIPELINE_TASK_CONTEXT")
    cands = [ctx_path] if ctx_path else []
    if run_dir:
        cands.append(os.path.join(run_dir, "task_context.json"))
    for c in cands:
        if not c:
            continue
        try:
            with open(c, encoding="utf-8-sig") as f:
                data = json.load(f)
            r = str(data.get("role", "")).strip().lower()
            if r:
                return r, f"file:{c}"
        except (OSError, ValueError, AttributeError):
            continue
    return "", ""


def get_ownership(cwd, run_dir):
    """Tra (owns: list|None, bugfix: bool). None = khong co thong tin -> rule nghi."""
    env = os.environ.get("AI_PIPELINE_OWNS")
    if env:
        return [p for p in env.split(os.pathsep) if p], _env_bugfix()
    cands = []
    if run_dir:
        cands.append(os.path.join(run_dir, "ownership.json"))
    for d in _walk_up(cwd):
        cand = os.path.join(d, ".ai-pipeline", "ownership.json")
        if os.path.isfile(cand):
            cands.append(cand)
            break
    for c in cands:
        try:
            with open(c, encoding="utf-8-sig") as f:
                data = json.load(f)
            owns = data.get("owns")
            bugfix = bool(data.get("bugfix", False)) or _env_bugfix()
            if owns is None:
                return None, bugfix
            return list(owns), bugfix
        except (OSError, ValueError, AttributeError):
            continue
    return None, _env_bugfix()


def _env_bugfix():
    return (os.environ.get("AI_PIPELINE_BUGFIX", "") or "").strip().lower() in ("1", "true", "yes")


def get_branch(cwd):
    env = (os.environ.get("AI_PIPELINE_BRANCH", "") or "").strip()
    if env:
        return env
    try:
        r = subprocess.run(
            ["git", "-C", os.path.abspath(cwd or os.getcwd()),
             "branch", "--show-current"],
            capture_output=True, text=True, timeout=5)
        name = (r.stdout or "").strip()
        return name or None
    except Exception:
        return None


def build_context(cwd, run_dir=None):
    cwd = os.path.abspath(cwd or os.getcwd())
    run_dir = run_dir or find_run_dir(cwd)
    role, role_src = get_role(run_dir)
    owns, bugfix = get_ownership(cwd, run_dir)
    return {
        "cwd": cwd,
        "run_dir": run_dir,
        "role": role,
        "role_src": role_src,
        "owns": owns,
        "bugfix": bugfix,
        "branch": get_branch(cwd),
    }


def event_paths(event):
    """Lay (tool, file_path|None, command|None) tu JSON su kien. Khong doan truong."""
    tool = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    fpath = tool_input.get("file_path", tool_input.get("path"))
    cmd = tool_input.get("command")
    return tool, (str(fpath) if fpath else None), (str(cmd) if cmd else None)


# --- Cac rule ---

def _deny(rule, reason):
    return {"allowed": False, "rule": rule, "reason": reason}


def _rule_label(tool, fpath, cmd, policy, ctx):
    if not policy["rules"].get("label_protection"):
        return None
    if ctx["role"] in ALLOWED_ROLES:
        return None
    protected = policy.get("protected_paths") or DEFAULT_POLICY["protected_paths"]
    hay = []
    if fpath:
        hay.append(norm(fpath))
    if tool == "Bash" and cmd:
        hay.append(norm(cmd))
    if tool not in FILE_TOOLS and tool != "Bash":
        return None
    if not hay:
        return None
    for prot in protected:
        token = norm(prot)
        if not token:
            continue
        for h in hay:
            if token in h:
                return _deny("label_protection",
                              f"duong dan nhan test duoc bao ve ({prot}); "
                              f"chi integrator/evaluator duoc cham (role hien tai: "
                              f"{ctx['role'] or 'khong ro'} tu {ctx['role_src'] or 'khong co'}). "
                              f"Can ngoai le? hoi coordinator qua ask.")
    return None


def _rule_install(tool, cmd, policy):
    if not policy["rules"].get("host_install"):
        return None
    if tool != "Bash" or not cmd:
        return None
    if not any(re.search(p, cmd, re.IGNORECASE) for p in INSTALL_PATTERNS):
        return None
    if any(re.search(p, cmd, re.IGNORECASE) for p in CONTAINER_HINTS):
        return None
    allowed_pkgs = [str(p).lower() for p in (policy.get("allowed_packages") or [])]
    if allowed_pkgs and any(p and p in cmd.lower() for p in allowed_pkgs):
        return None
    return _deny("host_install",
                 "cai goi tren host bi cam (pip/npm/yarn/apt/conda/brew/cargo/go). "
                 "Chi cai trong container (docker exec/run, ssh <host> docker ...) "
                 "sau khi human dong y, hoac them goi vao allowed_packages.")


def _rule_docker(tool, cmd, policy):
    if not policy["rules"].get("dangerous_docker"):
        return None
    if tool != "Bash" or not cmd:
        return None
    low = cmd.lower()
    if "docker" not in low:
        return None
    if "--privileged" in low:
        return _deny("dangerous_docker", "docker --privileged bi cam.")
    if re.search(r"--net(?:work)?(?:=|\s+)host\b", low):
        return _deny("dangerous_docker", "docker --net/--network host bi cam.")
    if re.search(r"\bdocker\s+system\s+prune\b", low):
        return _deny("dangerous_docker", "docker system prune bi cam.")
    m = re.search(r"\bdocker\s+rm\b([^|;]*)", low)
    if m and re.search(r"(?:^|\s)(?:-f|--force)\b", m.group(1)):
        names = [t for t in re.split(r"\s+", m.group(1).strip()) if t and not t.startswith("-")]
        prefix = str(policy.get("run_prefix") or DEFAULT_POLICY["run_prefix"])
        bad = [n for n in names if not n.startswith(prefix)]
        if bad or not names:
            return _deny("dangerous_docker",
                         f"docker rm -f chi duoc xoa container tien to '{prefix}' "
                         f"(vi pham: {', '.join(bad) if bad else 'khong ro ten'}).")
    return None


def _rule_ownership(tool, fpath, policy, ctx):
    if not policy["rules"].get("ownership"):
        return None
    if tool not in ("Edit", "Write"):
        return None
    owns = ctx.get("owns")
    if owns is None:
        return None  # thieu file ownership -> fail-open
    if not fpath:
        return None
    target = abs_of(fpath, ctx["cwd"])
    for own in owns:
        base = abs_of(own, ctx["cwd"])
        if target == base or _is_under(target, base):
            return None
    return _deny("ownership",
                 f"Edit/Write ngoai ownership cua task ({fpath}); "
                 f"owns hien tai: {', '.join(owns) if owns else '(rong)'}.")


def _is_test_path(fpath):
    n = norm(fpath)
    base = n.rsplit("/", 1)[-1]
    return ("/tests/" in n or n.startswith("tests/")
            or base.startswith("test_") or base.endswith("_test.py"))


def _rule_bugfix(tool, fpath, policy, ctx):
    if not policy["rules"].get("bugfix_tests"):
        return None
    if tool not in ("Edit", "Write"):
        return None
    if not ctx.get("bugfix"):
        return None
    if fpath and _is_test_path(fpath):
        return _deny("bugfix_tests",
                     f"task bugfix khong duoc sua file test ({fpath}) de lam xanh; "
                     f"sua source/code, khong sua test.")
    return None


def _rm_targets(cmd):
    """Tach cac muc tieu cua `rm -rf ...` trong chuoi lenh (khong shell)."""
    targets = []
    for chunk in re.split(r"[|;&\n]+", cmd):
        toks = chunk.split()
        for i, t in enumerate(toks):
            if t == "rm":
                rest = toks[i + 1:]
                flags = "".join(t for t in rest if t.startswith("-") and not t.startswith("--"))
                longflags = [t for t in rest if t.startswith("--")]
                if ("r" in flags and "f" in flags) or (
                        "--recursive" in longflags and ("f" in flags or "--force" in longflags)):
                    targets.extend(t for t in rest if not t.startswith("-"))
    return targets


def _rule_destructive(tool, cmd, policy, ctx):
    if not policy["rules"].get("destructive"):
        return None
    if tool != "Bash" or not cmd:
        return None
    if re.search(r"\bgit\b[^\n|;]*\bpush\b[^\n|;]*\s(--force|-f)\b", cmd):
        return _deny("destructive", "git push --force/-f bi cam.")
    if re.search(r"\bgit\b[^\n|;]*\breset\b[^\n|;]*--hard\b", cmd):
        mains = policy.get("main_branches") or DEFAULT_POLICY["main_branches"]
        branch = ctx.get("branch")
        if branch is None or branch in mains:
            extra = f"nhanh '{branch}'" if branch else "khong xac dinh duoc nhanh (fail-closed)"
            return _deny("destructive",
                         f"git reset --hard tren {extra} bi cam.")
        return None
    if re.search(r"(?<![a-zA-Z_/.-])rm\b", cmd):
        targets = _rm_targets(cmd)
        if targets:
            tmp = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
            worktree = os.path.abspath(
                os.environ.get("AI_PIPELINE_WORKTREE", "") or ctx["cwd"])
            for t in targets:
                if re.search(r"[\*\?\$\`\"']", t) or t in ("/", ".", "..", "~"):
                    return _deny("destructive",
                                 f"rm -rf muc tieu nguy hiem/khong xac dinh ({t}) bi cam.")
                resolved = abs_of(os.path.expanduser(t), ctx["cwd"])
                ok_tmp = _is_under(resolved, tmp) or os.path.normcase(resolved) == tmp
                ok_wt = _is_under(resolved, worktree) or resolved == worktree
                if not (ok_tmp or ok_wt):
                    return _deny("destructive",
                                 f"rm -rf ngoai thu muc tam/worktree bi cam ({t} -> {resolved}).")
        return None
    return None


def evaluate(event, policy, ctx):
    """Danh gia 1 su kien. Tra dict {allowed, rule, reason} (pure, de test)."""
    policy = policy or dict(DEFAULT_POLICY)
    tool, fpath, cmd = event_paths(event or {})
    for res in (
        _rule_label(tool, fpath, cmd, policy, ctx),
        _rule_install(tool, cmd, policy),
        _rule_docker(tool, cmd, policy),
        _rule_ownership(tool, fpath, policy, ctx),
        _rule_bugfix(tool, fpath, policy, ctx),
        _rule_destructive(tool, cmd, policy, ctx),
    ):
        if res is not None:
            return res
    return {"allowed": True, "rule": None, "reason": "khong vi pham rule nao"}


def audit(record, audit_path):
    try:
        if statefile is None:
            return False
        statefile.append_jsonl(audit_path, record)
        return True
    except Exception:
        return False


def decide(event, cwd, policy_path=None, audit_path=None, run_dir=None):
    """Danh gia + audit. Tra (result, audit_ok, audit_path)."""
    cwd = os.path.abspath(cwd or os.getcwd())
    policy_path = policy_path or find_policy(cwd)
    policy, corrupt = load_policy(policy_path)
    if corrupt:
        # fail-closed R1/R3 (dung mac dinh de chan), fail-open R4/R5 (tat).
        policy = dict(DEFAULT_POLICY)
        policy["rules"] = dict(DEFAULT_POLICY["rules"])
        policy["rules"]["ownership"] = False
        policy["rules"]["bugfix_tests"] = False
    ctx = build_context(cwd, run_dir)
    result = evaluate(event or {}, policy, ctx)
    tool, fpath, cmd = event_paths(event or {})
    audit_path = audit_path or find_audit_path(cwd, ctx["run_dir"])
    ok = audit({
        "ts": utc_now(),
        "guard": GUARD_VERSION,
        "tool": tool,
        "file": fpath,
        "command": (cmd[:500] if cmd else None),
        "allowed": result["allowed"],
        "rule": result["rule"],
        "reason": result["reason"],
        "role": ctx["role"],
        "policy": policy_path or "default",
        "policy_corrupt": corrupt,
    }, audit_path)
    return result, ok, audit_path


def _deny_output(result):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"[pipeline-guard:{result['rule']}] {result['reason']}"}}


def build_parser():
    p = argparse.ArgumentParser(description="Hook cuong che PreToolUse (ai-pipeline).")
    p.add_argument("--policy", default=None, help="duong dan guard_policy.json")
    p.add_argument("--audit", default=None, help="duong dan guard_audit.jsonl")
    p.add_argument("--cwd", default=None, help="thu muc lam viec (mac dinh: cwd hien tai)")
    p.add_argument("--run-dir", default=None, help="runs/<id> (mac dinh: tu tim)")
    p.add_argument("--check", action="store_true",
                   help="kiem tra thu cong/CI: doc event JSON tu stdin hoac --input")
    p.add_argument("--tool", default=None, help="cung --check: ten tool (Bash/Read/Edit/Write)")
    p.add_argument("--input", default=None, help="cung --check: tool_input JSON")
    p.add_argument("--input-file", default=None, help="cung --check: file chua event JSON")
    p.add_argument("--version", action="store_true", help="in phien ban guard")
    return p


def main(argv=None, stdin_text=None):
    a = build_parser().parse_args(argv)
    if a.version:
        print(GUARD_VERSION)
        return 0
    if a.input_file:
        try:
            with open(a.input_file, encoding="utf-8-sig") as f:
                raw = f.read()
        except OSError as e:
            print(f"pipeline-guard: khong doc duoc --input-file: {e}", file=sys.stderr)
            return 1
    elif a.input is not None:
        try:
            tool_input = json.loads(a.input)
        except ValueError as e:
            print(f"pipeline-guard: --input khong phai JSON: {e}", file=sys.stderr)
            return 1
        raw = json.dumps({"tool_name": a.tool or "Bash", "tool_input": tool_input})
    elif a.check and a.tool and stdin_text is None and sys.stdin.isatty():
        print("pipeline-guard: --check can event JSON tren stdin hoac --input/--input-file",
              file=sys.stderr)
        return 1
    else:
        raw = stdin_text
        if raw is None:
            try:
                raw = sys.stdin.read()
            except Exception as e:
                print(f"pipeline-guard: khong doc duoc stdin: {e}", file=sys.stderr)
                return 0
    try:
        event = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print("pipeline-guard: canh bao: event JSON khong doc duoc -> fail-open (cho qua, da audit).",
              file=sys.stderr)
        try:
            audit({"ts": utc_now(), "guard": GUARD_VERSION, "allowed": True,
                   "rule": None, "reason": "event JSON hong -> fail-open",
                   "policy_corrupt": False}, a.audit or find_audit_path(a.cwd, None))
        except Exception:
            pass
        return 0
    if not isinstance(event, dict):
        event = {}
    result, audit_ok, _ = decide(event, a.cwd or os.getcwd(),
                                 policy_path=a.policy, audit_path=a.audit,
                                 run_dir=a.run_dir)
    if result["allowed"]:
        if a.check:
            print(f"CHO PHEP: {result['reason']}")
        return 0
    msg = f"pipeline-guard CHAN [{result['rule']}]: {result['reason']}"
    if not audit_ok:
        msg += " (canh bao: khong ghi duoc audit)"
    if a.check:
        print(msg)
    print(msg, file=sys.stderr)
    print(json.dumps(_deny_output(result), ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.exit(main())
