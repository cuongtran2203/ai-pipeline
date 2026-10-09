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
  R1 label_protection : bao ve nhan test (seal) tru integrator/evaluator,
                        gom ca tim kiem DE QUY (rg/grep -r/git grep/find.../
                        findstr /s/dir /s/Select-String -Recurse)
                        phu len file nhan.
  R2 host_install     : cam cai goi tren host (cho phep trong container).
                        Chi xet tu-lenh that (bo qua chuoi trong quote cua
                        lenh khac nhu echo/git commit/grep); allowed_packages
                        so khop CHINH XAC theo argv (moi goi deu duyet).
  R3 dangerous_docker : cam docker nguy hiem.
  R4 ownership        : chi sua trong duong dan owns cua task.
  R5 bugfix_tests     : task bugfix khong duoc sua tests/ de lam xanh.
  R6 destructive      : cam lenh pha hoai (rm -rf ngoai tmp/worktree,
                        push --force/-f/refspec '+'/'--mirror', reset --hard).

Role CHI lay tu task trong task_context.json {"tasks": {...}} do
`pipeline_guard.py context write <run_dir>` sinh tu plan.json; task duoc chon
bang AI_PIPELINE_TASK hoac ten worktree <run_id>-<task_id> (chu thuong,
so khop khong phan biet hoa thuong). Bien moi truong AI_PIPELINE_ROLE BI BO
(RV5: tin cay yeu, tu khai); truong role trong JSON su kien hoac tool_input
BI BO QUA (chong gia mao).

Policy hong -> fail-closed: dung policy mac dinh de chan R1/R3, DENY nhom
ghi (Edit/Write/Bash) kem thong diep cau hinh ro rang, van cho Read thuong;
tat R4/R5 (thieu ownership/task-context -> bo qua). Xem README.

Moi quyet dinh (allow/deny + ly do + rule id) append vao guard_audit.jsonl
qua scripts/statefile.py. Audit hong khong lam hong quyet dinh chan/cho.

Che do thu cong/CI (Codex khong co hook tuong duong):
    echo '<event-json>' | python scripts/pipeline_guard.py --check
    python scripts/pipeline_guard.py --check --tool Bash \
        --input '{"command":"pip install x"}' --cwd .
    python scripts/pipeline_guard.py context write runs/<id>  # cap role/owns
        # theo plan.json -> <run>/task_context.json (coordinator chay 1 lan
        # truoc khi start worker; worker dat AI_PIPELINE_TASK=<task_id>)

Stdlib only. Da nen tang Linux/macOS/Windows (pathlib/os.path,
subprocess khong shell, encoding utf-8).
"""
import argparse
import fnmatch
import glob
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

GUARD_VERSION = "fh2-v1"
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

INSTALL_PATTERNS = [  # giu de tuong thich nguoc (doc hieu); R2 moi dung _segment_installs
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

# --- Tach lenh shell (dung chung cho R1/R2/R6): chi xet tu-lenh that,
# bo qua chuoi nam trong quote cua lenh khac (echo 'pip install...'). ---
# Gioi han da biet (ghi trong skill ai-pipeline-hooks): base64/sh -d,
# bien shell ($X=pip), noi chuoi, eval, goi binary ma hoa van vong qua.

WRAPPER_WORDS = ("bash", "sh", "zsh", "dash", "python", "env", "sudo",
                 "cmd", "powershell", "pwsh")

# tu-lenh -> cac token con (subcommand) duoc coi la "cai goi".
# easy_install: ban than lenh da la cai dat (khong can subcommand).
INSTALL_SUBCMDS = {
    "pip": ("install", "download"),
    "python": ("-m pip install", "-m pip download"),
    "npm": ("install", "i"),
    "yarn": ("add",),
    "apt": ("install",),
    "conda": ("install",),
    "brew": ("install",),
    "cargo": ("install",),
    "go": ("install",),
    "pipx": ("install", "inject"),
    "easy_install": (),
    "uv": ("add", "sync"),
    "poetry": ("add", "install"),
    "pdm": ("add", "install"),
}


def _canon_word(word):
    """Chuan hoa ten lenh: basename, ha chu, bo .exe/.cmd/.bat, nhan dang
    bien the python3.x/pip3.x. Tra None khi khong phai installer/wrapper."""
    base = re.split(r"[\\/]", word or "")[-1].lower()
    base = re.sub(r"\.(exe|cmd|bat|ps1)$", "", base)
    if re.fullmatch(r"python3(\.\d+)?", base):
        return "python"
    if re.fullmatch(r"pip3(\.\d+)?", base):
        return "pip"
    if base in ("python", "pip", "pip3"):
        return base
    if base == "apt-get":
        return "apt"
    if base in INSTALL_SUBCMDS or base in WRAPPER_WORDS:
        return base
    return None


def _strip_heredocs(cmd):
    """Bo than heredoc (<<EOF ... EOF) de khoi quet nham noi dung van ban."""
    out = []
    delim = None
    for line in str(cmd).splitlines():
        if delim is not None:
            if line.strip() == delim:
                delim = None
            continue
        m = re.search(r"<<-?\s*['\"]?([A-Za-z0-9_]+)['\"]?", line)
        if m:
            delim = m.group(1)
            out.append(line[:m.start()])
            continue
        out.append(line)
    return "\n".join(out)


def _split_commands(cmd):
    """Tach chuoi shell thanh cac lenh don theo ; && || | & va newline,
    ton trong quote don/kep (khong xu ly backslash nhu shell that)."""
    segs, buf, q = [], [], None
    chars = _strip_heredocs(cmd)
    i, n = 0, len(chars)
    while i < n:
        c = chars[i]
        if q:
            buf.append(c)
            if c == q:
                q = None
        elif c in ("'", '"'):
            q = c
            buf.append(c)
        elif c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(chars[i + 1])
            i += 1
        elif c in (";", "\n"):
            segs.append("".join(buf))
            buf = []
        elif c == "&":
            segs.append("".join(buf))
            buf = []
            if chars[i + 1:i + 2] == "&":
                i += 1
        elif c == "|":
            segs.append("".join(buf))
            buf = []
            if chars[i + 1:i + 2] == "|":
                i += 1
        else:
            buf.append(c)
        i += 1
    segs.append("".join(buf))
    return [s.strip() for s in segs if s.strip()]


def _qtok(seg):
    """Tach lenh don thanh token theo khoang trang ngoai quote; bo quote
    bao quanh (giu backslash nguyen de an toan duong dan Windows)."""
    toks, buf, q = [], [], None
    for c in seg:
        if q:
            buf.append(c)
            if c == q:
                q = None
        elif c in ("'", '"'):
            q = c
            buf.append(c)
        elif c in (" ", "\t"):
            if buf:
                toks.append("".join(buf))
                buf = []
        else:
            buf.append(c)
    if buf:
        toks.append("".join(buf))
    out = []
    for t in toks:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
            t = t[1:-1]
        out.append(t)
    return out


def _payload_after(flag_toks, argv):
    """Lay payload dung sau co -c/--command//c... (bo flag + gia tri cua no)."""
    low = [a.lower() for a in argv]
    for j, a in enumerate(low):
        if a in flag_toks and j + 1 < len(argv):
            return argv[j + 1]
    return None


def _payload_installs(payload, depth):
    """True khi payload (-c/--command//c) chua >=1 tu-lenh cai goi.
    Payload co the ghep nhieu lenh (; && || |) nen phai tach va quet tung
    lenh, de quy co gioi han do sau (qua _segment_installs)."""
    if not payload:
        return False
    return any(_segment_installs(s, depth + 1)
               for s in _split_commands(payload))


def _strip_assignments(argv):
    """Bo tien to VAR=... (env) roi tra phan con lai."""
    i = 0
    while i < len(argv) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[i]):
        i += 1
    return argv[i:]


def _segment_installs(seg, depth=0):
    """True khi 1 lenh don cai goi tren host (khong tinh container)."""
    if depth > 3:
        return False
    argv = _qtok(seg)
    if not argv:
        return False
    word = _canon_word(argv[0])
    rest = argv[1:]
    low_seg = seg.lower()
    if word == "docker" and re.search(r"\b(exec|run)\b", low_seg):
        return False  # cai trong container: cho phep
    if word == "ssh" and "docker" in low_seg:
        return False  # ssh ... docker ...: cho phep
    if word in ("bash", "sh", "zsh", "dash"):
        return _payload_installs(_payload_after(("-c", "--command"), argv), depth)
    if word == "python":
        payload = _payload_after(("-c", "--command"), argv)
        if payload is not None:
            return _payload_installs(payload, depth)
        low = [a.lower() for a in rest]
        for i, a in enumerate(low):
            if a == "-m" and i + 1 < len(low) and low[i + 1] == "pip":
                tail = low[i + 2:]
                if "install" in tail or "download" in tail:
                    return True
        return False
    if word == "env":
        rest2 = _strip_assignments(
            _drop_leading_flags(rest, ("-u", "--unset")))
        return bool(rest2) and any(
            _segment_installs(s, depth + 1)
            for s in _split_commands(" ".join(rest2)))
    if word == "sudo":
        rest2 = _drop_leading_flags(
            rest, ("-u", "-g", "-h", "-p", "-r", "-t", "-U"))
        return bool(rest2) and any(
            _segment_installs(s, depth + 1)
            for s in _split_commands(" ".join(rest2)))
    if word == "cmd":
        return _payload_installs(_payload_after(("/c", "/k"), argv), depth)
    if word in ("powershell", "pwsh"):
        return _payload_installs(_payload_after(("-command", "-c"), argv), depth)
    if word == "uv":
        # uv add/uv sync; hoi quy FH2: `uv pip install/sync/download x`
        # truoc bi chan, phai chan lai (uv pip la wrapper cua pip).
        low = [a.lower() for a in rest]
        if "add" in low or "sync" in low:
            return True
        if "pip" in low and any(s in low for s in ("install", "download", "sync")):
            return True
        return False
    if word in INSTALL_SUBCMDS:
        subs = INSTALL_SUBCMDS[word]
        if not subs:  # easy_install: goi la cai
            return True
        low = [a.lower() for a in rest]
        if word == "python":
            return False  # (da xu ly -m pip o tren; tranh nham)
        return any(s in low for s in subs if " " not in s)
    return False


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


def _context_candidates(run_dir):
    ctx_path = os.environ.get("AI_PIPELINE_TASK_CONTEXT")
    cands = [ctx_path] if ctx_path else []
    if run_dir:
        cands.append(os.path.join(run_dir, "task_context.json"))
    return [c for c in cands if c]


def load_context_doc(run_dir):
    """Doc task_context.json (moi: {"tasks": {...}}; cu: {"role":...}).
    Tra dict hoac {}."""
    for c in _context_candidates(run_dir):
        try:
            with open(c, encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return {}


def resolve_task_id(cwd, tasks, run_id=None):
    """Chon task: AI_PIPELINE_TASK truoc; khong co thi khop ten worktree/
    thu muc cwd dang <run_id>-<task_id> (plan_to_herdr dat ten worktree
    `--name <run_id>-<task_id lowercase>`, vd. `rr-i1` cho run `rr` task `I1`).
    So khop KHONG phan biet hoa thuong; ho tro run_id co dau '-'.
    Khong suy tu tham so trong event. Tra "" neu khong ra."""
    tid = (os.environ.get("AI_PIPELINE_TASK") or "").strip()
    if tid:
        for k in tasks or {}:
            if str(k).lower() == tid.lower():
                return str(k)
        return tid
    names = []
    try:
        names.append(os.path.basename(os.path.abspath(cwd or os.getcwd())))
    except Exception:
        pass
    wt = os.environ.get("AI_PIPELINE_WORKTREE", "")
    if wt:
        names.append(os.path.basename(os.path.abspath(wt)))
    keys = [str(k) for k in (tasks or {})]
    run_low = str(run_id or "").strip().lower()
    for name in names:
        low = (name or "").lower()
        if not low:
            continue
        for k in keys:
            if low == k.lower():
                return k
        for k in keys:
            kl = k.lower()
            suffix = "-" + kl
            if not low.endswith(suffix):
                continue
            if run_low:
                # worktree chuan <run_id>-<task_id>: tien to phai la run_id
                # (khong phan biet hoa thuong); dung ca khi run_id co '-'.
                if low == run_low + suffix or low.endswith(run_low + suffix):
                    return k
                continue
            return k
    return ""


def get_role(run_dir, cwd=None, context_doc=None):
    """Role CHI tu task_context.json (do `context write` sinh tu plan.json).
    AI_PIPELINE_ROLE (bien moi truong tu khai) BI BO (RV5: tin cay yeu)."""
    doc = context_doc if context_doc is not None else load_context_doc(run_dir)
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if isinstance(tasks, dict) and tasks:
        tid = resolve_task_id(cwd or os.getcwd(), tasks,
                              run_id=doc.get("run_id") if isinstance(doc, dict) else None)
        if tid:
            entry = tasks.get(tid)
            if isinstance(entry, dict):
                r = str(entry.get("role", "")).strip().lower()
                if r:
                    src = "env:AI_PIPELINE_TASK" if (os.environ.get("AI_PIPELINE_TASK") or "").strip() else "worktree-dir"
                    return r, f"task:{tid} ({src})"
            return "", ""  # co context nhung khong ra task -> fail-closed
        return "", ""
    r = str(doc.get("role", "")).strip().lower() if isinstance(doc, dict) else ""
    if r:
        return r, "file:task_context.json (legacy)"
    return "", ""


def get_ownership(cwd, run_dir, context_doc=None):
    """Tra (owns: list|None, bugfix: bool). None = khong co thong tin -> rule nghi."""
    env = os.environ.get("AI_PIPELINE_OWNS")
    if env:
        return [p for p in env.split(os.pathsep) if p], _env_bugfix()
    doc = context_doc if context_doc is not None else load_context_doc(run_dir)
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if isinstance(tasks, dict) and tasks:
        tid = resolve_task_id(cwd, tasks,
                              run_id=doc.get("run_id") if isinstance(doc, dict) else None)
        entry = tasks.get(tid) if tid else None
        if isinstance(entry, dict):
            owns = entry.get("owns")
            bugfix = bool(entry.get("bugfix", False)) or _env_bugfix()
            if owns is None:
                return None, bugfix
            return list(owns), bugfix
        return None, _env_bugfix()
    if isinstance(doc, dict) and ("owns" in doc or "bugfix" in doc):
        owns = doc.get("owns")
        bugfix = bool(doc.get("bugfix", False)) or _env_bugfix()
        if owns is None:
            return None, bugfix
        return list(owns), bugfix
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
    doc = load_context_doc(run_dir)
    role, role_src = get_role(run_dir, cwd, doc)
    owns, bugfix = get_ownership(cwd, run_dir, doc)
    return {
        "cwd": cwd,
        "run_dir": run_dir,
        "role": role,
        "role_src": role_src,
        "owns": owns,
        "bugfix": bugfix,
        "branch": get_branch(cwd),
    }


def find_plan(run_dir):
    """Tim plan.json cua run: <run_dir>/plan.json, roi artifacts/*/plan.json."""
    if run_dir:
        direct = os.path.join(run_dir, "plan.json")
        if os.path.isfile(direct):
            return direct
        cands = sorted(glob.glob(os.path.join(run_dir, "artifacts", "*", "plan.json")))
        if cands:
            return cands[0]
    return None


def context_write(run_dir):
    """Doc plan.json, ghi <run_dir>/task_context.json {tasks: {id: {role,
    owns, bugfix}}} bang statefile.update_json (atomic). Tra (so task, path)."""
    if statefile is None:
        raise RuntimeError("thieu scripts/statefile.py: khong the ghi atomic.")
    if not run_dir or not os.path.isdir(run_dir):
        raise RuntimeError(f"run dir khong ton tai: {run_dir!r}")
    plan_path = find_plan(os.path.abspath(run_dir))
    if not plan_path:
        raise RuntimeError(
            f"khong tim thay plan.json trong {run_dir} (can <run>/plan.json "
            f"hoac artifacts/*/plan.json).")
    with open(plan_path, encoding="utf-8-sig") as f:
        plan = json.load(f)
    tasks = {}
    for t in plan.get("tasks", []):
        if not isinstance(t, dict) or not t.get("id"):
            continue
        tid = str(t["id"])
        tasks[tid] = {
            "role": str(t.get("role", "") or ""),
            "owns": list(t.get("owns", []) or []),
            "bugfix": bool(t.get("bugfix", False)),
        }
    out_path = os.path.join(os.path.abspath(run_dir), "task_context.json")

    def fn(old):
        base = dict(old) if isinstance(old, dict) else {}
        base["tasks"] = tasks
        base["run_id"] = plan.get("run_id", base.get("run_id", ""))
        base["plan"] = os.path.basename(plan_path)
        return base

    try:
        statefile.update_json(out_path, fn, default={})
    except AttributeError:
        raise RuntimeError("statefile khong co update_json.")
    return len(tasks), out_path


def cmd_context_write(argv):
    """Thuc thi: pipeline_guard.py context write <run_dir>."""
    run_dir = None
    for a in argv or []:
        if not a.startswith("-"):
            run_dir = a
            break
    if not run_dir:
        run_dir = os.environ.get("AI_PIPELINE_RUN_DIR") or find_run_dir(os.getcwd())
    try:
        n, path = context_write(run_dir)
    except (OSError, ValueError, RuntimeError) as e:
        print(f"pipeline-guard context write: LOI: {e}", file=sys.stderr)
        return 1
    print(f"pipeline-guard context write: {n} task -> {path}")
    return 0


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


# --- R1: bao ve nhan + chan tim kiem de quy phu len file nhan ---

# tu-lenh tim kiem (da chuan hoa, khong phan biet hoa thuong PowerShell).
SEARCH_READER_WORDS = ("cat", "grep", "egrep", "rg", "ag", "head", "tail",
                        "less", "more", "sed", "awk", "strings",
                        "select-string", "xargs")


def _search_base(argv0):
    base = re.split(r"[\\/]", argv0 or "")[-1].lower()
    return re.sub(r"\.(exe|cmd|bat|ps1)$", "", base)


def _drop_leading_flags(argv, take_val=()):
    """Bo cac flag dung DAU (vd. sudo -u root cmd): giu nguyen phan sau."""
    j = 0
    while j < len(argv) and argv[j].startswith("-") and len(argv[j]) > 1:
        if argv[j] in take_val:
            j += 2
        else:
            j += 1
    return argv[j:]


def _unwrap_leading(seg):
    """Bo tien to sudo/env de lay lenh that (phuc vu R1/R6)."""
    argv = _qtok(seg)
    for _ in range(3):
        if not argv:
            break
        w = _search_base(argv[0])
        if w == "sudo":
            argv = _drop_leading_flags(
                argv[1:], ("-u", "-g", "-h", "-p", "-r", "-t", "-U"))
        elif w == "env":
            rest = _drop_leading_flags(argv[1:], ("-u", "--unset"))
            argv = _strip_assignments(rest)
        else:
            break
    return " ".join(argv)


def _git_root(cwd):
    d = os.path.abspath(cwd or os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.abspath(cwd or os.getcwd())
        d = parent


def _trailing_paths(args, skip_first=True):
    """Duong dan tim kiem = cac arg khong-phai-flag, bo arg dau (pattern)."""
    nonflags = [a for a in args if not a.startswith("-") or a in (".", "..")]
    if not nonflags:
        return []
    paths = nonflags[1:] if skip_first else nonflags
    return paths or ["."]


def _win_flag(tok):
    """True khi token la flag kieu Windows (/s, /i, /c:...). Chi nhan flag
    1 chu cai (findstr/dir) de khong nham duong dan tuyet doi POSIX (/labels)."""
    t = tok or ""
    if re.fullmatch(r"/[a-zA-Z](:.*)?", t):
        return True
    return t.lower() == "/off[line]"


def _win_paths(args, skip_first=True):
    """Duong dan cho findstr/dir: bo flag -... va /...; bo arg dau (pattern)."""
    nonflags = [a for a in args
                if a in (".", "..") or (not a.startswith("-") and not _win_flag(a))]
    if not nonflags:
        return ["."]
    paths = nonflags[1:] if skip_first else nonflags
    return paths or ["."]


def _select_string_roots(args):
    roots = []
    i = 0
    skip_next = False
    while i < len(args):
        a = args[i]
        if skip_next:
            skip_next = False
            i += 1
            continue
        low = a.lower()
        if low in ("-path", "-literalpath"):
            if i + 1 < len(args):
                roots.extend(p for p in args[i + 1].split(",") if p)
            i += 2
            continue
        if low.startswith(("-path:", "-literalpath:")):
            roots.extend(p for p in a.split(":", 1)[1].split(",") if p)
            i += 1
            continue
        if low in ("-pattern", "-include", "-exclude"):
            skip_next = True
            i += 1
            continue
        if not a.startswith("-"):
            roots.append(a)
        i += 1
    return roots or ["."]


def _segment_recursive_search(seg, cwd, piped_to_reader=False):
    """Nhan dien tim kiem de quy trong 1 lenh don.
    Tra (is_recursive: bool, roots: list[str])."""
    seg = _unwrap_leading(seg)
    argv = _qtok(seg)
    if not argv:
        return False, []
    base = _search_base(argv[0])
    rest = argv[1:]
    low = seg.lower()
    if base == "docker" and re.search(r"\b(exec|run)\b", low):
        return False, []  # tim trong container, khong phai nhan host
    if base == "ssh" and "docker" in low:
        return False, []
    if base in ("rg", "ag"):
        paths = _trailing_paths(rest)
        return (True, paths) if paths else (False, [])
    if base in ("grep", "egrep"):
        rec = any(re.fullmatch(r"-+[a-zA-Z]*[rR][a-zA-Z]*", a) or a == "--recursive"
                  for a in rest if a.startswith("-"))
        if not rec:
            return False, []
        paths = _trailing_paths(rest)
        return True, (paths or ["."])
    if base == "git" and rest[:1] == ["grep"]:
        return True, [_git_root(cwd)]
    if base == "find":
        roots = []
        for a in rest:
            if a.startswith("-") or a in ("!", "(", ")", ","):
                break
            roots.append(a)
        roots = roots or ["."]
        exec_hit = False
        for j, a in enumerate(rest):
            if a in ("-exec", "-execdir", "-ok", "-okdir") and j + 1 < len(rest):
                if _search_base(rest[j + 1]) in SEARCH_READER_WORDS:
                    exec_hit = True
        if exec_hit or piped_to_reader:
            return True, roots
        return False, []
    if base == "findstr":
        toks = [a.lower() for a in rest]
        if "/s" not in toks:
            return False, []
        return True, _win_paths(rest)
    if base == "dir":
        # `dir /s` liet ke de quy tren Windows (mac dinh thu muc hien tai).
        # Khac findstr: dir khong co pattern dau, moi arg con lai la path.
        toks = [a.lower() for a in rest]
        if "/s" not in toks:
            return False, []
        return True, _win_paths(rest, skip_first=False)
    if base == "select-string":
        if "-recurse" not in [a.lower() for a in rest]:
            return False, []
        return True, _select_string_roots(rest)
    if base in ("get-childitem", "gci"):
        if "-recurse" not in [a.lower() for a in rest]:
            return False, []
        if not piped_to_reader:
            return False, []
        return True, _select_string_roots(rest)
    return False, []


def _label_bases(ctx):
    bases = []
    for b in (ctx.get("cwd"), ctx.get("run_dir"), _git_root(ctx.get("cwd"))):
        if not b:
            continue
        ab = os.path.abspath(b)
        if ab not in bases:
            bases.append(ab)
    return bases


def _protected_locations(protected, bases):
    locs = []
    for tok in protected or []:
        t = str(tok).strip().replace("\\", "/").strip()
        if not t:
            continue
        if os.path.isabs(t) or re.match(r"^[A-Za-z]:", t):
            locs.append(os.path.normpath(t))
            continue
        core = t.strip("/")
        if not core:
            continue
        for b in bases:
            locs.append(os.path.normpath(os.path.join(b, core)))
    return locs


def _covers(root, loc):
    """True khi root tim kiem bao phu (to tien-hoac-bang) vi tri nhan."""
    try:
        if os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(loc)):
            return True
    except Exception:
        pass
    if _is_under(loc, root):
        return True
    try:  # xuyen symlink (POSIX that, Windows skip khi khong tao duoc)
        rr, ll = os.path.realpath(root), os.path.realpath(loc)
        if os.path.normcase(rr) == os.path.normcase(ll):
            return True
        return _is_under(ll, rr)
    except Exception:
        return False


def _exclude_patterns(seg):
    pats = []
    argv = _qtok(seg)
    i = 0
    while i < len(argv):
        a = argv[i]
        low = a.lower()
        if low.startswith(("--exclude=", "--glob=", "--exclude-dir=")):
            pats.append(a.split("=", 1)[1])
        elif low.startswith(("-g",)) and len(a) > 2 and not low.startswith("--"):
            pats.append(a[2:])
        elif low in ("--exclude", "--glob", "--exclude-dir", "-g",
                     "-not"):
            if low == "-not" and i + 2 < len(argv) and argv[i + 1].lower() in (
                    "-path", "-name", "-wholename"):
                pats.append(argv[i + 2])
                i += 3
                continue
            if i + 1 < len(argv):
                pats.append(argv[i + 1])
            i += 2
            continue
        elif ":!" in a:  # pathspec git (:!pattern)
            pats.append(a.split(":!", 1)[1].strip("\"'"))
        i += 1
    return [p for p in pats if p]


def _has_valid_exclude(seg, protected):
    """True khi lenh da loai nhan bang glob/--exclude hop le (khop that)."""
    pats = _exclude_patterns(seg)
    if not pats:
        return False
    toks = [str(t).strip().replace("\\", "/").lower() for t in protected or []]
    for p in pats:
        pn = str(p).strip().replace("\\", "/").lower().strip("\"'")
        if not pn:
            continue
        core = pn.strip("*?[]!/")
        for tok in toks:
            if not tok:
                continue
            base = tok.rsplit("/", 1)[-1]
            if (core and (core in tok or core in base or tok in pn)
                    or fnmatch.fnmatch(tok, pn) or fnmatch.fnmatch(base, pn)):
                return True
    return False


def _rule_label_recursive(cmd, policy, ctx):
    protected = policy.get("protected_paths") or DEFAULT_POLICY["protected_paths"]
    segs = _split_commands(cmd)
    words = set()
    for s in segs:
        av = _qtok(s)
        if av:
            words.add(_search_base(av[0]))
    piped_reader = bool(words & set(SEARCH_READER_WORDS))
    bases = _label_bases(ctx)
    locs = _protected_locations(protected, bases)
    if not locs:
        return None
    for seg in segs:
        rec, roots = _segment_recursive_search(seg, ctx.get("cwd"), piped_reader)
        if not rec:
            continue
        for r in roots:
            rr = r.strip("\"'")
            if not rr:
                continue
            root = os.path.expanduser(rr)
            if not os.path.isabs(root):
                root = os.path.normpath(os.path.join(ctx.get("cwd") or os.getcwd(), root))
            else:
                root = os.path.normpath(root)
            hit = next((loc for loc in locs if _covers(root, loc)), None)
            if hit is None:
                continue
            if _has_valid_exclude(seg, protected):
                continue
            return _deny("label_protection",
                          f"tim kiem de quy phu len duong dan nhan test ({root} bao phu {hit}); "
                          f"chi integrator/evaluator duoc quet nhan (role hien tai: "
                          f"{ctx['role'] or 'khong ro'}). Hay tim trong thu muc con cu the "
                          f"khong chua file nhan, hoac loai nhan bang --glob/--exclude "
                          f"(vd. --glob '!*sealed*'). Can ngoai le? hoi coordinator qua ask.")
    return None


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
    if tool == "Bash" and cmd:
        # (Bash xu ly rieng ben duoi: mien tru glob/--exclude + de quy.)
        pass
    else:
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
    if tool == "Bash" and cmd:
        # Mien tru: token nhan chi xuat hien trong glob/--exclude hop le
        # (vd. --glob '!eval_manifest.json') thi khong deny truc tiep o day;
        # kiem tra de quy ben duoi se cho qua neu exclude khop that.
        segs = _split_commands(cmd)
        for prot in protected:
            token = norm(prot)
            if not token:
                continue
            hit_segs = [s for s in segs if token in norm(s)]
            if not hit_segs:
                continue
            if all(_has_valid_exclude(s, [prot]) for s in hit_segs):
                continue
            return _deny("label_protection",
                          f"duong dan nhan test duoc bao ve ({prot}); "
                          f"chi integrator/evaluator duoc cham (role hien tai: "
                          f"{ctx['role'] or 'khong ro'} tu {ctx['role_src'] or 'khong co'}). "
                          f"Can ngoai le? hoi coordinator qua ask.")
        return _rule_label_recursive(cmd, policy, ctx)
    return None


# --- allowed_packages: parse ten goi theo argv (RV5: khong substring) ---

def _norm_pkg(name):
    """Chuan hoa ten goi de so khop chinh xac (PEP 503: _/-/hoa-thuong)."""
    return re.sub(r"[-_.]+", "-", str(name or "").strip()).strip("-").lower()


def _strip_pkg_spec(spec):
    """Ten goi tu 1 spec argv (loai ==/>=/extras/marker). Tra None khi khong
    phai ten goi xac dinh (URL, git+, duong dan, file, -r/-e...)."""
    s = str(spec or "").strip().strip("\"'")
    if not s:
        return None
    if "://" in s:
        return None  # URL truc tiep
    if s.lower().startswith(("http:", "https:", "git+", "file:", "ftp:")):
        return None
    if re.match(r"^[A-Za-z]:[\\/]", s) or s.startswith(("~", "/", "\\", "./", "../", ".\\")):
        return None  # duong dan
    if s.startswith("@"):
        # scope npm: @scope/pkg[@version] -> @scope/pkg
        m = re.fullmatch(r"(@[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+)(@.+)?", s)
        return _norm_pkg(m.group(1)) if m else None
    if "/" in s or "\\" in s:
        return None  # duong dan / file archive
    if s.lower().endswith((".whl", ".tar.gz", ".zip", ".tgz", ".git")):
        return None
    s = s.split(";")[0].strip()  # marker moi truong (PEP 508)
    if "@" in s:
        # PEP 508 truc tiep (pkg @ url) -> khong kiem duoc; npm foo@1.2 -> foo
        head, _, tail = s.partition("@")
        if not head.strip() or "://" in tail or "/" in tail:
            return None
        s = head.strip()
    # pypi: cat extras [a,b] va version (==/>=/<=/!=/~=/>/</===)
    s = re.split(r"\[", s, 1)[0]
    s = re.split(r"===|==|>=|<=|~=|!=|>|<", s, 1)[0].strip()
    if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?", s or ""):
        return None
    return _norm_pkg(s) or None


# flag nhan gia tri (bo ca gia tri dung sau) khi parse goi cai dat.
_PKG_VALUE_FLAGS = frozenset({
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable",
    "-t", "--target", "--prefix", "--root", "--src",
    "-i", "--index-url", "--extra-index-url", "--find-links", "-f",
    "--trusted-host", "--config-settings", "--global-option", "--build-option",
    "--constraint", "--config-setting", "--python-version", "--platform",
    "--abi", "--implementation", "--only-binary", "--no-binary",
    "-g", "--global", "--registry", "--scope", "--auth",
})


def _unverifiable_flag(tok):
    """True khi flag keo theo noi dung khong kiem duoc (-r file, -e path/URL,
    -c constraints): gap la DENY, khong allow."""
    return tok.lower() in ("-r", "--requirement", "-c", "--constraint",
                           "-e", "--editable")


def _collect_pkgs(args):
    """Lay ten goi tu argv sau subcommand install/add. Tra list (co the rong)
    hoac None khi gap nguon khong kiem duoc (file/URL/editable)."""
    pkgs = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pkgs.extend(args[i + 1:])
            break
        if a.startswith("-") and len(a) > 1:
            name, eq, _val = a.partition("=")
            if _unverifiable_flag(name):
                return None
            if eq or name.lower() in _PKG_VALUE_FLAGS:
                if not eq:
                    i += 1  # bo gia tri dung sau
            i += 1
            continue
        pkgs.append(a)
        i += 1
    out = []
    for p in pkgs:
        n = _strip_pkg_spec(p)
        if n is None:
            return None
        out.append(n)
    return out


def _install_start(argv):
    """Vi tri bat dau danh sach goi: (installer, args-sau-subcommand).
    Tra (None, None) khi khong nhan dang."""
    if not argv:
        return None, None
    word = _canon_word(argv[0])
    rest = argv[1:]
    if word == "sudo":
        rest = _drop_leading_flags(rest, ("-u", "-g", "-h", "-p", "-r", "-t", "-U"))
        return _install_start(rest)
    if word == "env":
        rest = _strip_assignments(_drop_leading_flags(rest, ("-u", "--unset")))
        return _install_start(rest)
    if word in ("bash", "sh", "zsh", "dash", "cmd", "powershell", "pwsh"):
        return None, None  # wrapper: goi _install_packages theo tung lenh tach
    if word == "python":
        low = [a.lower() for a in rest]
        for i, a in enumerate(low):
            if a == "-m" and i + 1 < len(low) and low[i + 1] == "pip":
                tail = rest[i + 2:]
                for j, t in enumerate(tail):
                    if t.lower() in ("install", "download"):
                        return "pip", tail[j + 1:]
                return "pip", []
        return None, None
    if word == "uv" and rest[:1] and rest[0].lower() == "pip":
        tail = rest[1:]
        for j, t in enumerate(tail):
            if t.lower() in ("install", "download", "sync"):
                return "uv-pip", tail[j + 1:]
        return "uv-pip", []
    if word in INSTALL_SUBCMDS:
        subs = INSTALL_SUBCMDS[word]
        if not subs:
            return word, list(rest)  # easy_install: toan bo la goi
        low = [a.lower() for a in rest]
        hit = next((s for s in subs if " " not in s and s in low), None)
        if hit is None:
            return None, None
        return word, rest[low.index(hit) + 1:]
    return None, None


def _install_packages(seg, depth=0):
    """Ten goi (da chuan hoa) cua 1 lenh don cai goi. Tra None khi khong phai
    cai goi hoac khong kiem duoc (wrapper phuc tap, -r file, URL...)."""
    if depth > 3:
        return None
    argv = _qtok(seg)
    if not argv:
        return None
    word = _canon_word(argv[0])
    rest = argv[1:]
    if word in ("bash", "sh", "zsh", "dash"):
        payload = _payload_after(("-c", "--command"), argv)
        if not payload:
            return None
        all_pkgs = []
        for s in _split_commands(payload):
            if not _segment_installs(s, depth + 1):
                continue
            pkgs = _install_packages(s, depth + 1)
            if pkgs is None:
                return None
            all_pkgs.extend(pkgs)
        return all_pkgs or None
    if word == "python":
        payload = _payload_after(("-c", "--command"), argv)
        if payload is not None:
            all_pkgs = []
            for s in _split_commands(payload):
                if not _segment_installs(s, depth + 1):
                    continue
                pkgs = _install_packages(s, depth + 1)
                if pkgs is None:
                    return None
                all_pkgs.extend(pkgs)
            return all_pkgs or None
    if word in ("cmd",):
        payload = _payload_after(("/c", "/k"), argv)
        if payload is None:
            return None
        all_pkgs = []
        for s in _split_commands(payload):
            if not _segment_installs(s, depth + 1):
                continue
            pkgs = _install_packages(s, depth + 1)
            if pkgs is None:
                return None
            all_pkgs.extend(pkgs)
        return all_pkgs or None
    if word in ("powershell", "pwsh"):
        payload = _payload_after(("-command", "-c"), argv)
        if payload is None:
            return None
        all_pkgs = []
        for s in _split_commands(payload):
            if not _segment_installs(s, depth + 1):
                continue
            pkgs = _install_packages(s, depth + 1)
            if pkgs is None:
                return None
            all_pkgs.extend(pkgs)
        return all_pkgs or None
    if word in ("sudo", "env"):
        inst, args = _install_start(argv)
        if inst is None:
            return None
        pkgs = _collect_pkgs(args or [])
        return pkgs or None
    inst, args = _install_start(argv)
    if inst is None:
        return None
    pkgs = _collect_pkgs(args or [])
    return pkgs or None


def _rule_install(tool, cmd, policy):
    if not policy["rules"].get("host_install"):
        return None
    if tool != "Bash" or not cmd:
        return None
    install_segs = [s for s in _split_commands(cmd) if _segment_installs(s)]
    if not install_segs:
        return None
    # allow CHI khi MOI goi cua MOI tu-lenh cai dat deu nam trong danh sach
    # da duyet (so khop chinh xac sau chuan hoa; -r file/URL/editable -> deny).
    allowed = {_norm_pkg(p) for p in (policy.get("allowed_packages") or [])}
    allowed.discard("")
    if allowed:
        ok = True
        for s in install_segs:
            pkgs = _install_packages(s)
            if not pkgs or any(p not in allowed for p in pkgs):
                ok = False
                break
        if ok:
            return None
    return _deny("host_install",
                 "cai goi tren host bi cam (pip/pipx/npm/yarn/apt/conda/brew/cargo/go/"
                 "uv/poetry/pdm, ke ca download). "
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
    """Tach cac muc tieu cua `rm -rf ...` theo tung lenh don (da bo quote)."""
    targets = []
    for seg in _split_commands(cmd):
        argv = _qtok(seg)
        for i, t in enumerate(argv):
            if _search_base(t) != "rm":
                continue
            rest = argv[i + 1:]
            short, longs, paths = "", [], []
            for tok in rest:
                if tok == "--":
                    continue
                if tok.startswith("--") and len(tok) > 2:
                    longs.append(tok.lower())
                elif tok.startswith("-") and len(tok) > 1:
                    short += tok[1:]
                else:
                    paths.append(tok)
            if ("r" in short or "--recursive" in longs) and (
                    "f" in short or "--force" in longs):
                targets.extend(paths)
    return targets


def _temp_roots():
    roots = []
    try:
        roots.append(tempfile.gettempdir())
    except Exception:
        pass
    roots.append("/tmp")
    roots.append("/var/folders")
    for k in ("TEMP", "TMP", "TMPDIR"):
        v = os.environ.get(k)
        if v:
            roots.append(v)
    return [r for r in roots if r]


def _strictly_under(path, root):
    try:
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(root)):
            return False
        return _is_under(path, root)
    except Exception:
        return False


def _rm_target_reason(t, cwd, worktree):
    """None = duoc phep; chuoi = ly do cam (R6)."""
    raw = (t or "").strip()
    if not raw or raw in ("/", "\\", ".", "..", "~"):
        return "rm -rf muc tieu nguy hiem/khong xac dinh (%s) bi cam." % (t,)
    if re.fullmatch(r"[A-Za-z]:[\\/]?", raw):
        return "rm -rf goc o dia (%s) bi cam." % (t,)
    if raw.startswith("~"):
        return "rm -rf thu muc home (%s) bi cam." % (t,)
    if ".." in re.split(r"[\\/]", raw):
        return "rm -rf chua '..' (%s) bi cam (hay chi thu muc con cu the)." % (t,)
    if re.search(r"[*?$\"'%]", raw) or chr(96) in raw:
        return "rm -rf ky tu dac biet/glob/bien moi truong (%s) bi cam." % (t,)
    resolved = os.path.expanduser(raw)
    if not os.path.isabs(resolved):
        resolved = os.path.join(cwd, resolved)
    resolved = os.path.normpath(resolved)
    if re.fullmatch(r"[A-Za-z]:[\\/]?", resolved) or resolved in ("/", "\\"):
        return "rm -rf goc he thong (%s -> %s) bi cam." % (t, resolved)
    cwd_abs = os.path.abspath(cwd)
    if os.path.normcase(resolved) == os.path.normcase(cwd_abs):
        return "rm -rf chinh thu muc lam viec (%s) bi cam." % (t,)
    if _is_under(cwd_abs, resolved):
        return "rm -rf to tien cua thu muc lam viec (%s -> %s) bi cam." % (t, resolved)
    try:
        real_res = os.path.realpath(resolved)
    except Exception:
        real_res = resolved
    for root in _temp_roots():
        try:
            labs = os.path.normpath(os.path.abspath(os.path.expanduser(root)))
            if _strictly_under(resolved, labs) or _strictly_under(
                    real_res, os.path.realpath(labs)):
                return None  # ben trong thu muc tam he thong: cho phep
        except Exception:
            continue
    try:
        wt = os.path.abspath(worktree)
        if (os.path.normcase(resolved) == os.path.normcase(wt)
                or _is_under(resolved, wt)
                or os.path.normcase(real_res) == os.path.normcase(os.path.realpath(wt))
                or _is_under(real_res, os.path.realpath(wt))):
            return None  # trong worktree: cho phep
    except Exception:
        pass
    return "rm -rf ngoai thu muc tam/worktree bi cam (%s -> %s)." % (t, resolved)


def _git_subcommand(argv):
    """Bo git-global flags (-C/-c/...) roi tra (subcommand, args-sau)."""
    rest = list(argv[1:])
    i = 0
    take_val = {"-C", "--git-dir", "--work-tree", "--namespace", "-c",
                "--super-prefix", "--config-env"}
    while i < len(rest):
        a = rest[i]
        if a in take_val:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a, rest[i + 1:]
    return None, []


def _rule_destructive(tool, cmd, policy, ctx):
    if not policy["rules"].get("destructive"):
        return None
    if tool != "Bash" or not cmd:
        return None
    for seg in _split_commands(cmd):
        argv = _qtok(_unwrap_leading(seg))
        if argv and _search_base(argv[0]) == "git":
            sub, args = _git_subcommand(argv)
            if sub == "push":
                if "--mirror" in args:
                    return _deny("destructive", "git push --mirror bi cam.")
                if "--force" in args or "-f" in args:
                    return _deny("destructive", "git push --force/-f bi cam.")
                if any(a.startswith("+") and len(a) > 1 for a in args):
                    return _deny("destructive",
                                  "git push refspec '+' (ghi de nhanh) bi cam.")
            if sub == "reset" and "--hard" in [a.lower() for a in args]:
                mains = policy.get("main_branches") or DEFAULT_POLICY["main_branches"]
                branch = ctx.get("branch")
                if branch is None or branch in mains:
                    extra = "nhanh '%s'" % branch if branch else "khong xac dinh duoc nhanh (fail-closed)"
                    return _deny("destructive",
                                  "git reset --hard tren %s bi cam." % extra)
    if re.search(r"(?<![a-zA-Z_/.-])rm\b", cmd):
        targets = _rm_targets(cmd)
        if targets:
            worktree = os.path.abspath(
                os.environ.get("AI_PIPELINE_WORKTREE", "") or ctx["cwd"])
            for t in targets:
                reason = _rm_target_reason(t, ctx["cwd"], worktree)
                if reason is not None:
                    return _deny("destructive", reason)
        return None
    return None
# --- P3 (RV5): policy hong -> deny nhom ghi, van cho Read thuong ---
WRITE_TOOLS_CORRUPT = ("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash")


def _rule_policy_corrupt(tool, corrupt, policy_src):
    """Policy hong: fail-closed cho nhom ghi (Edit/Write/Bash) kem thong diep
    cau hinh ro rang; Read thuong van cho qua. Audit van ghi (trong decide)."""
    if not corrupt:
        return None
    if tool in WRITE_TOOLS_CORRUPT:
        where = policy_src or "guard_policy.json"
        return _deny("policy_config",
                     f"cau hinh guard hong ({where} khong doc duoc); fail-closed: "
                     f"tu choi {tool} de an toan. Sua/xoa file policy roi chay lai. "
                     f"Chi Read duoc phep; can ngoai le? hoi coordinator qua ask.")
    return None


def evaluate(event, policy, ctx, corrupt=False, policy_src=None):
    """Danh gia 1 su kien. Tra dict {allowed, rule, reason} (pure, de test)."""
    policy = policy or dict(DEFAULT_POLICY)
    tool, fpath, cmd = event_paths(event or {})
    for res in (
        _rule_policy_corrupt(tool, corrupt, policy_src),
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
        # fail-closed: dung mac dinh de chan R1/R3; tat R4/R5 (thieu
        # ownership/task-context -> bo qua); rule policy_config (trong
        # evaluate) deny nhom ghi, van cho Read thuong.
        policy = dict(DEFAULT_POLICY)
        policy["rules"] = dict(DEFAULT_POLICY["rules"])
        policy["rules"]["ownership"] = False
        policy["rules"]["bugfix_tests"] = False
    ctx = build_context(cwd, run_dir)
    result = evaluate(event or {}, policy, ctx,
                      corrupt=corrupt, policy_src=policy_path)
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
    raw = list(argv) if argv is not None else sys.argv[1:]
    if len(raw) >= 2 and raw[0] == "context" and raw[1] == "write":
        return cmd_context_write(raw[2:])
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
