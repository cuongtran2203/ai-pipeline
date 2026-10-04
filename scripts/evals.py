#!/usr/bin/env python3
"""EVAL cho chính cấu hình agent (skill / AGENTS.md / roles / mirror / subcommand).

Hai tầng:
(A) STATIC  - kiểm tra cấu hình agent bằng Python thuần, nhanh, KHÔNG cần agent:
              chạy trong CI / pre-commit (`evals.py run --static`).
(B) BEHAVIORAL - case `evals/cases/*.jsonl`, chạy trên agent thật ở chế độ
              headless và chấm bằng scorer rule; `--replay <transcript>` chấm lại
              hoàn toàn offline/xác định.

INCIDENT -> EVAL:
  evals.py add-incident --run-dir runs/<id> --title "..." --from-notebook <entry id>
  (đọc mục sổ type error/decision, sinh case khung, ghi cạnh kg `evidenced_by`
  Incident -> Eval artifact qua API đã kiểm của kg.py).

TRIGGER:
  evals.py run --changed [--base REF]  -> nếu skills/, roles/, AGENTS.md, templates/,
  scripts/pipeline_guard.py đổi thì chạy static + liệt kê case hành vi liên quan.

Nguyên tắc: KHÔNG tự chạy hàng loạt tốn tiền. Mặc định chỉ liệt kê; chỉ chạy đúng
1 case khi có `--behavior --agent <...> --case <ID>`. Stdlib only, đa nền tảng.
"""
import argparse
import datetime as dt
import fnmatch
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import kg  # noqa: E402  (validated knowledge-graph write API)
import statefile  # noqa: E402  (atomic/append-only file primitives)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - older/odd streams
    pass

DEFAULT_RUN_DIR = os.path.join(ROOT, "runs", "ai-pipeline-v2")
DEFAULT_CASES_DIR = os.path.join(ROOT, "evals", "cases")

# Đổi những đường dẫn này thì phải chạy lại eval cấu hình (trigger).
TRIGGER_PREFIXES = (
    "skills/",
    "roles/",
    "templates/",
    "scripts/pipeline_guard.py",
    "AGENTS.md",
)
# Quy tắc bắt buộc của AGENTS.md phải còn xuất hiện trong skill điều phối.
REQUIRED_RULES = {
    "skills/ai-pipeline/SKILL.md": ("G1", "G2", "G3", "sandbox", "report.md", "report.html"),
    "skills/ai-pipeline-orca/SKILL.md": ("G2", "G3", "worktree"),
}
SCORERS = ("must_call", "must_not_call", "must_mention", "must_ask_before")
CASE_SOURCES = ("incident", "manual")

# A ref must look like a real file of one of the framework dirs (with an
# extension), so prose like "skills/roles/templates" is never mistaken for a path.
PATH_REF_RE = re.compile(r"\b(scripts|templates|schemas|roles)/([A-Za-z0-9_.-]+\.[A-Za-z0-9]+)")
CMD_REF_RE = re.compile(r"scripts/(\w+)\.py\s+([a-zA-Z][a-zA-Z0-9_-]*)(?=[\s`)\],]|$)")
# Known G3 contradiction: docs must tie G3 to needs_g3 (train HOẶC gpu), not "mode=train only".
RESTRICTIVE_RE = re.compile(r"ch[ỉi]\s+khi|only\s+when|ch[ỉi]\s+dùng")


class EvalFormatError(ValueError):
    """Case/transcript sai định dạng (thông báo phải chỉ rõ file:dòng)."""


# --------------------------------------------------------------------------- #
# STATIC
# --------------------------------------------------------------------------- #
def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fm = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fm
        if ":" in line:
            key, value = line.split(":", 1)
            fm[key.strip()] = value.strip()
    return None


def check_frontmatter(root):
    problems = []
    skills = os.path.join(root, "skills")
    if not os.path.isdir(skills):
        return ["thư mục skills/ không tồn tại"]
    for name in sorted(os.listdir(skills)):
        d = os.path.join(skills, name)
        if not os.path.isdir(d):
            continue
        sk = os.path.join(d, "SKILL.md")
        if not os.path.isfile(sk):
            problems.append("skills/%s/SKILL.md thiếu" % name)
            continue
        fm = _frontmatter(_read_text(sk))
        if fm is None:
            problems.append("skills/%s/SKILL.md thiếu frontmatter ---" % name)
            continue
        if fm.get("name") != name:
            problems.append("skills/%s: frontmatter name='%s' không khớp tên thư mục"
                            % (name, fm.get("name")))
        if not (fm.get("description") or "").strip():
            problems.append("skills/%s: frontmatter thiếu description" % name)
    return problems


def check_mirror(root):
    try:
        import sync_skills
    except Exception as exc:  # pragma: no cover
        return ["không import được sync_skills: %s" % exc]
    problems = []
    src_root = os.path.join(root, "skills")
    if not os.path.isdir(src_root):
        return ["thư mục skills/ không tồn tại"]
    names = sorted(n for n in os.listdir(src_root) if os.path.isdir(os.path.join(src_root, n)))
    for target in (".claude/skills", ".agents/skills"):
        base = os.path.join(root, *target.split("/"))
        for name in names:
            if not sync_skills.in_sync(os.path.join(src_root, name), os.path.join(base, name)):
                problems.append("%s/%s lệch so với skills/%s (chạy sync_skills.py)" % (target, name, name))
    return problems


def _docs(root):
    """Docs whose referenced paths/commands/rules are audited."""
    out = []
    agents = os.path.join(root, "AGENTS.md")
    if os.path.isfile(agents):
        out.append("AGENTS.md")
    for pattern in ("skills/*/SKILL.md", "roles/*.md"):
        for p in sorted(glob.glob(os.path.join(root, pattern))):
            out.append(os.path.relpath(p, root).replace("\\", "/"))
    return out


def check_referenced_paths(root):
    problems = []
    for rel in _docs(root):
        text = _read_text(os.path.join(root, *rel.split("/")))
        for m in PATH_REF_RE.finditer(text):
            token = m.group(0)
            if not os.path.exists(os.path.join(root, *token.split("/"))):
                problems.append("%s: nhắc tới '%s' nhưng không tồn tại" % (rel, token))
    return problems


def _script_subcommands(path, cwd):
    """Subcommands parsed from `--help`, or None when the script has none/unreadable."""
    try:
        r = subprocess.run([sys.executable, path, "--help"], cwd=cwd,
                           capture_output=True, text=True, encoding="utf-8",
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (r.stdout or "").splitlines():
        if line.lower().startswith("usage:"):
            m = re.search(r"\{([^}]*)\}", line)
            if m:
                return {s.strip() for s in m.group(1).split(",") if s.strip()}
            return None
    return None


def check_subcommands(root):
    problems = []
    cache = {}
    for rel in _docs(root):
        text = _read_text(os.path.join(root, *rel.split("/")))
        for m in CMD_REF_RE.finditer(text):
            script, sub = m.group(1), m.group(2)
            sp = os.path.join(root, "scripts", script + ".py")
            if not os.path.isfile(sp):
                continue  # the path check already reports the missing script
            if script not in cache:
                cache[script] = _script_subcommands(sp, root)
            subs = cache[script]
            if subs is None:
                continue
            if sub not in subs:
                problems.append("%s: 'python scripts/%s.py %s' không có subcommand này (có: %s)"
                                % (rel, script, sub, ", ".join(sorted(subs))))
    return problems


def check_roles(root):
    """Every role declared in roles/registry.json must have roles/<name>.md."""
    path = os.path.join(root, "roles", "registry.json")
    if not os.path.isfile(path):
        return ["roles/registry.json không tồn tại"]
    try:
        data = json.loads(_read_text(path))
    except ValueError as exc:
        return ["roles/registry.json: JSON lỗi: %s" % exc]
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict):
        return ["roles/registry.json: thiếu object 'roles'"]
    problems = []
    for name in sorted(roles):
        if not os.path.isfile(os.path.join(root, "roles", name + ".md")):
            problems.append("roles/registry.json khai role '%s' nhưng thiếu roles/%s.md" % (name, name))
    return problems


def check_mandatory_rules(root):
    problems = []
    for rel, words in REQUIRED_RULES.items():
        path = os.path.join(root, *rel.split("/"))
        if not os.path.isfile(path):
            problems.append("thiếu %s" % rel)
            continue
        low = _read_text(path).lower()
        for word in words:
            if word.lower() not in low:
                problems.append("%s: thiếu quy tắc bắt buộc '%s'" % (rel, word))
    return problems


def _coordination_docs(root):
    """Coordination docs: AGENTS.md + the two orchestrator skills (scope of the
    mandatory-rule/contradiction checks)."""
    out = []
    if os.path.isfile(os.path.join(root, "AGENTS.md")):
        out.append("AGENTS.md")
    for rel in REQUIRED_RULES:
        if os.path.isfile(os.path.join(root, *rel.split("/"))):
            out.append(rel)
    return out


def check_contradictions(root):
    problems = []
    for rel in _coordination_docs(root):
        for i, line in enumerate(_read_text(os.path.join(root, *rel.split("/"))).splitlines(), 1):
            if "G3" not in line:
                continue
            low = line.lower()
            has_needs = any(k in low for k in ("needs_g3", "gpu", "compute"))
            if RESTRICTIVE_RE.search(low) and "train" in low and "mode" in low and not has_needs:
                problems.append("%s:%d: mâu thuẫn G3 (nói G3 chỉ khi mode=train nhưng thiếu needs_g3/GPU)"
                                % (rel, i))
    return problems


def check_json(root):
    problems = []
    for folder, must_schema in (("schemas", True), ("templates", False)):
        d = os.path.join(root, folder)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            try:
                data = json.loads(_read_text(os.path.join(d, name)))
            except ValueError as exc:
                problems.append("%s/%s: JSON lỗi: %s" % (folder, name, exc))
                continue
            if must_schema and isinstance(data, dict) and not any(
                    k in data for k in ("$schema", "type", "properties", "allOf", "anyOf")):
                problems.append("%s/%s: không giống JSON schema (thiếu type/properties/$schema)"
                                % (folder, name))
    return problems


STATIC_CHECKS = (
    ("frontmatter", "frontmatter name+description khớp tên skill", check_frontmatter),
    ("mirror", "mirror .claude/.agents đồng bộ (sync_skills)", check_mirror),
    ("paths", "mọi đường dẫn scripts/templates/schemas/roles được nhắc đều tồn tại", check_referenced_paths),
    ("roles", "mọi role trong roles/registry.json có file", check_roles),
    ("subcommands", "mọi 'python scripts/X.py <sub>' có subcommand", check_subcommands),
    ("mandatory", "G1/G2/G3 + sandbox/worktree/báo cáo còn trong skill điều phối", check_mandatory_rules),
    ("contradictions", "không có mâu thuẫn từ khoá đã biết (G3 vs needs_g3)", check_contradictions),
    ("json", "schema/template JSON hợp lệ", check_json),
)


def run_static(root):
    """Return [(check_id, ok, problems)]; ok=False when the check has problems."""
    results = []
    for cid, _desc, fn in STATIC_CHECKS:
        problems = fn(root)
        results.append((cid, not problems, problems))
    return results


def print_static(root):
    print("=== EVAL TĨNH - cấu hình agent (%s) ===" % root)
    results = run_static(root)
    descriptions = dict((cid, desc) for cid, desc, _fn in STATIC_CHECKS)
    failed = 0
    for cid, ok, problems in results:
        print("[%s] %-14s %s" % ("OK" if ok else "LỖI", cid, descriptions.get(cid, cid)))
        for p in problems:
            print("      - %s" % p)
        if not ok:
            failed += 1
    print("--- KẾT QUẢ: %s (%d/%d check đạt) ---"
          % ("PASS" if not failed else "FAIL", len(results) - failed, len(results)))
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# BEHAVIORAL: case + scorer rule
# --------------------------------------------------------------------------- #
def _validate_case(obj, where):
    if not isinstance(obj, dict):
        raise EvalFormatError("%s: case phải là object JSON" % where)
    for key in ("id", "prompt", "expect"):
        if key not in obj:
            raise EvalFormatError("%s: thiếu khóa '%s'" % (where, key))
    if not isinstance(obj["id"], str) or not obj["id"].strip():
        raise EvalFormatError("%s: 'id' phải là chuỗi khác rỗng" % where)
    if not isinstance(obj["prompt"], str) or not obj["prompt"].strip():
        raise EvalFormatError("%s: 'prompt' phải là chuỗi khác rỗng" % where)
    source = obj.get("source", "manual")
    if source not in CASE_SOURCES:
        raise EvalFormatError("%s: 'source' lạ '%s' (hợp lệ: %s)" % (where, source, ", ".join(CASE_SOURCES)))
    if not isinstance(obj["expect"], list):
        raise EvalFormatError("%s: 'expect' phải là list scorer" % where)
    for i, sc in enumerate(obj["expect"]):
        if not isinstance(sc, dict) or "scorer" not in sc:
            raise EvalFormatError("%s: expect[%d] phải là object có 'scorer'" % (where, i))
        kind = sc["scorer"]
        if kind not in SCORERS:
            raise EvalFormatError("%s: expect[%d] scorer lạ '%s' (hợp lệ: %s)"
                                  % (where, i, kind, ", ".join(SCORERS)))
        if kind in ("must_call", "must_not_call", "must_mention"):
            if not isinstance(sc.get("pattern"), str) or not sc["pattern"]:
                raise EvalFormatError("%s: expect[%d] %s cần 'pattern' (chuỗi regex)"
                                      % (where, i, kind))
        if kind == "must_ask_before":
            for key in ("ask_pattern", "before_pattern"):
                if not isinstance(sc.get(key), str) or not sc[key]:
                    raise EvalFormatError("%s: expect[%d] must_ask_before cần '%s'" % (where, i, key))


def load_cases(cases_dir):
    """Load every `*.jsonl` case; raise EvalFormatError with file:line on bad input."""
    cases = []
    for path in sorted(glob.glob(os.path.join(cases_dir, "*.jsonl"))):
        base = os.path.basename(path)
        with open(path, encoding="utf-8") as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError as exc:
                    raise EvalFormatError("%s:%d: JSON lỗi: %s" % (base, ln, exc))
                _validate_case(obj, "%s:%d" % (base, ln))
                obj.setdefault("source", "manual")
                obj["_file"] = base
                cases.append(obj)
    ids = [c["id"] for c in cases]
    dupes = sorted(set(i for i in ids if ids.count(i) > 1))
    if dupes:
        raise EvalFormatError("id case bị trùng: %s" % ", ".join(dupes))
    return cases


def find_case(cases, case_id):
    for c in cases:
        if c["id"] == case_id:
            return c
    raise EvalFormatError("không tìm thấy case id='%s'" % case_id)


def print_list(cases_dir):
    try:
        cases = load_cases(cases_dir)
    except EvalFormatError as exc:
        print("LỖI ĐỊNH DẠNG CASE: %s" % exc, file=sys.stderr)
        return 1
    print("=== CASE HÀNH VI (%d) trong %s ===" % (len(cases), cases_dir))
    for c in cases:
        print("  %-34s %-9s %s" % (c["id"], c.get("source", "manual"),
                                   c.get("description", "")[:70]))
    print("Chạy 1 case: python scripts/evals.py run --behavior --agent <orca id|claude|codex|command-code> --case <ID>")
    return 0


class Transcript(object):
    """Chuẩn hoá transcript: raw text + tool_calls + messages (đủ để chấm rule)."""

    def __init__(self, case_id="", agent="", prompt="", raw="", tool_calls=None, messages=None):
        self.case_id = case_id
        self.agent = agent
        self.prompt = prompt
        self.raw = raw or ""
        self.tool_calls = list(tool_calls or [])
        self.messages = list(messages or [])

    @property
    def text(self):
        parts = [self.raw] + [str(m.get("text", "")) for m in self.messages]
        return "\n".join(p for p in parts if p)

    def to_dict(self):
        return {"case_id": self.case_id, "agent": self.agent, "prompt": self.prompt,
                "raw": self.raw, "tool_calls": self.tool_calls, "messages": self.messages}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise EvalFormatError("transcript phải là object JSON")
        return cls(case_id=data.get("case_id", ""), agent=data.get("agent", ""),
                   prompt=data.get("prompt", ""), raw=data.get("raw", ""),
                   tool_calls=data.get("tool_calls"), messages=data.get("messages"))


TOOL_CALL_RE = re.compile(r"^\s*(?:\[tool\]|tool[_:])\s*([A-Za-z_][\w.-]*)\s*[:(]\s*(.+)$",
                          re.MULTILINE | re.IGNORECASE)


def transcript_from_raw(case_id, agent, prompt, raw):
    calls = [{"tool": m.group(1), "input": m.group(2).strip()} for m in TOOL_CALL_RE.finditer(raw)]
    return Transcript(case_id=case_id, agent=agent, prompt=prompt, raw=raw, tool_calls=calls,
                      messages=[{"role": "assistant", "text": raw}])


def load_transcript(path):
    text = _read_text(path)
    try:
        return Transcript.from_dict(json.loads(text))
    except ValueError:
        # A raw agent log is a valid transcript too.
        return Transcript(raw=text)


def _find_call(transcript, pattern, tool=None):
    for call in transcript.tool_calls:
        if tool and str(call.get("tool", "")).lower() != tool.lower():
            continue
        if re.search(pattern, str(call.get("input", "")), re.IGNORECASE):
            return call
    if pattern and re.search(pattern, transcript.text, re.IGNORECASE):
        return {"tool": tool or "?", "input": transcript.text}
    return None


def score_scorer(transcript, sc):
    kind = sc["scorer"]
    if kind in ("must_call", "must_not_call"):
        found = _find_call(transcript, sc.get("pattern"), sc.get("tool"))
        tool = sc.get("tool") or "bất kỳ"
        if kind == "must_call":
            ok = found is not None
            reason = "phải gọi %s khớp /%s/" % (tool, sc.get("pattern"))
        else:
            ok = found is None
            reason = "không được gọi %s khớp /%s/" % (tool, sc.get("pattern"))
        return ok, reason
    if kind == "must_mention":
        ok = re.search(sc["pattern"], transcript.text, re.IGNORECASE) is not None
        return ok, "phải nhắc /%s/" % sc["pattern"]
    if kind == "must_ask_before":
        ask = re.search(sc["ask_pattern"], transcript.text, re.IGNORECASE)
        before = re.search(sc["before_pattern"], transcript.text, re.IGNORECASE)
        if before is None:
            return True, "không làm /%s/ nên không cần hỏi trước" % sc["before_pattern"]
        if ask is None:
            return False, "phải hỏi /%s/ trước /%s/" % (sc["ask_pattern"], sc["before_pattern"])
        ok = ask.start() < before.start()
        return ok, "hỏi /%s/ phải đứng trước /%s/" % (sc["ask_pattern"], sc["before_pattern"])
    raise EvalFormatError("scorer lạ '%s'" % kind)


def score_case(case, transcript):
    rows = []
    for sc in case.get("expect", []):
        ok, reason = score_scorer(transcript, sc)
        rows.append((sc["scorer"], ok, reason))
    return all(ok for _, ok, _ in rows), rows


def _append_result(run_dir, record):
    try:
        os.makedirs(os.path.join(os.path.abspath(run_dir), "evals"), exist_ok=True)
        statefile.append_jsonl(os.path.join(os.path.abspath(run_dir), "evals", "results.jsonl"), record)
    except Exception:  # noqa: BLE001 - logging kết quả không được làm hỏng chấm
        print("Cảnh báo: không ghi được evals/results.jsonl (%s)" % run_dir, file=sys.stderr)


AGENT_COMMANDS = {
    "claude": ["claude", "-p"],
    "codex": ["codex", "exec"],
    "command-code": ["command-code", "-p", "--trust", "--no-session", "--accept-edits"],
    "pi": ["pi", "-p"],
}


def cmd_behavior(a):
    cases_dir = os.path.abspath(a.cases_dir or DEFAULT_CASES_DIR)
    try:
        cases = load_cases(cases_dir)
        case = find_case(cases, a.case)
    except EvalFormatError as exc:
        print("LỖI: %s" % exc, file=sys.stderr)
        return 1
    if a.agent not in AGENT_COMMANDS and not a.agent_cmd:
        print("LỖI: agent '%s' chưa có recipe headless; dùng --agent-cmd '<lệnh ...> {}' "
              "hoặc agent khác (%s)" % (a.agent, ", ".join(sorted(AGENT_COMMANDS))), file=sys.stderr)
        return 1
    base_cmd = a.agent_cmd.split() if a.agent_cmd else list(AGENT_COMMANDS[a.agent])
    prompt = case["prompt"]
    cmd = base_cmd + [prompt] if "{}" not in base_cmd else [x.replace("{}", prompt) for x in base_cmd]
    run_dir = os.path.abspath(a.run_dir or DEFAULT_RUN_DIR)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(run_dir, "evals", stamp)
    os.makedirs(out_dir, exist_ok=True)
    print("Chạy case '%s' trên agent '%s' (1 case)... " % (case["id"], a.agent))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=a.timeout)
        raw = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    except (OSError, subprocess.SubprocessError) as exc:
        print("LỖI: không chạy được agent: %s" % exc, file=sys.stderr)
        return 1
    raw_path = os.path.join(out_dir, case["id"] + ".log")
    with open(raw_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
    transcript = transcript_from_raw(case["id"], a.agent, prompt, raw)
    tpath = os.path.join(out_dir, case["id"] + ".transcript.json")
    with open(tpath, "w", encoding="utf-8", newline="\n") as f:
        json.dump(transcript.to_dict(), f, ensure_ascii=False, indent=2)
    passed, rows = score_case(case, transcript)
    _print_score(case, passed, rows)
    _append_result(run_dir, {"ts": stamp, "case": case["id"], "agent": a.agent,
                             "mode": "behavior", "pass": passed})
    print("transcript: %s" % tpath)
    return 0 if passed else 1


def cmd_replay(a):
    cases_dir = os.path.abspath(a.cases_dir or DEFAULT_CASES_DIR)
    try:
        cases = load_cases(cases_dir)
        transcript = load_transcript(a.replay)
        case_id = a.case or transcript.case_id
        case = find_case(cases, case_id)
    except EvalFormatError as exc:
        print("LỖI: %s" % exc, file=sys.stderr)
        return 1
    passed, rows = score_case(case, transcript)
    _print_score(case, passed, rows)
    if a.run_dir:
        _append_result(os.path.abspath(a.run_dir),
                       {"ts": dt.datetime.now().strftime("%Y%m%d-%H%M%S"), "case": case["id"],
                        "agent": transcript.agent, "mode": "replay", "pass": passed})
    return 0 if passed else 1


def _print_score(case, passed, rows):
    print("=== CASE %s: %s ===" % (case["id"], "PASS" if passed else "FAIL"))
    for scorer, ok, reason in rows:
        print("  [%s] %-14s %s" % ("OK" if ok else "X", scorer, reason))
    if not rows:
        print("  (case khung: 'expect' rỗng - chưa chấm được gì)")


# --------------------------------------------------------------------------- #
# TRIGGER
# --------------------------------------------------------------------------- #
def _git(root, args):
    try:
        r = subprocess.run(["git"] + args, cwd=root, capture_output=True, text=True,
                           encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except OSError:
        return ""
    return r.stdout if r.returncode == 0 else ""


def changed_files(root, base=None):
    """Files changed in the working tree vs `base` (default HEAD) + untracked."""
    out = set()
    diff = ["diff", "--name-only", base] if base else ["diff", "--name-only", "HEAD"]
    out.update(x.strip() for x in _git(root, diff).splitlines() if x.strip())
    out.update(x.strip() for x in _git(root, ["ls-files", "--others", "--exclude-standard"]).splitlines() if x.strip())
    return sorted(x.replace("\\", "/") for x in out)


def is_eval_relevant(paths):
    for path in paths:
        p = path.replace("\\", "/")
        for trig in TRIGGER_PREFIXES:
            if trig.endswith("/"):
                if p.startswith(trig):
                    return True
            elif p == trig:
                return True
    return False


def related_cases(cases, paths):
    related = []
    for c in cases:
        triggers = c.get("trigger") or []
        for trig in triggers:
            if any(fnmatch.fnmatch(p, trig) for p in paths):
                related.append(c)
                break
    return related


def cmd_changed(root, base):
    paths = changed_files(root, base)
    print("=== EVAL THEO THAY ĐỔI (git %s) ===" % (base or "HEAD"))
    if not paths:
        print("Không có thay đổi trong working tree.")
        return 0
    for p in paths:
        print("  ~ %s" % p)
    if not is_eval_relevant(paths):
        print("Không đổi skills/roles/AGENTS.md/templates/pipeline_guard.py -> không cần eval cấu hình.")
        return 0
    print("-> Có thay đổi cấu hình agent: chạy EVAL TĨNH, chỉ LIỆT KÊ case hành vi liên quan.")
    code = print_static(root)
    try:
        cases = load_cases(os.path.join(root, "evals", "cases"))
    except EvalFormatError as exc:
        print("LỖI ĐỊNH DẠNG CASE: %s" % exc, file=sys.stderr)
        return 1
    rel = related_cases(cases, paths)
    if rel:
        print("Case hành vi liên quan (%d):" % len(rel))
        for c in rel:
            print("  - %s (%s)" % (c["id"], c.get("source", "manual")))
    else:
        print("Không có case hành vi liên quan.")
    return code


# --------------------------------------------------------------------------- #
# INCIDENT -> EVAL
# --------------------------------------------------------------------------- #
def _read_journal(run_dir):
    path = os.path.join(os.path.abspath(run_dir), "notebook", "journal.jsonl")
    if not os.path.isfile(path):
        raise EvalFormatError("không thấy sổ thí nghiệm: %s" % path)
    return statefile.read_jsonl(path, strict=True)


def find_notebook_entry(run_dir, entry_id):
    for entry in _read_journal(run_dir):
        if entry.get("id") == entry_id:
            return entry
    raise EvalFormatError("không thấy mục sổ id='%s' trong %s" % (entry_id, run_dir))


def ensure_incident_entity(run_dir, entry):
    """Return the Incident entity id for a notebook entry (match by source_key/title)."""
    for eid, ent in kg.read_entities(run_dir).items():
        if ent.get("type") != "Incident":
            continue
        props = ent.get("properties") or {}
        if props.get("source_key") == entry.get("id") or ent.get("title") == entry.get("title"):
            return eid
    eid = "incident:" + kg.slug(entry.get("id") or entry.get("title") or "incident")
    kg.upsert_entity(run_dir, eid, "Incident", entry.get("title") or "Incident",
                     body=entry.get("body", ""),
                     properties={"source_key": entry.get("id"), "origin": "notebook"},
                     created_at=entry.get("ts"))
    return eid


def _unique_case_id(cases_dir, base):
    candidate = base
    n = 2
    while glob.glob(os.path.join(cases_dir, candidate + ".jsonl")):
        candidate = "%s-%d" % (base, n)
        n += 1
    return candidate


def add_incident(run_dir, title, entry_id, cases_dir=None, case_id=None, no_kg=False, root=ROOT):
    """Sinh case khung từ một mục sổ, ghi file và (tuỳ chọn) cạnh KG `evidenced_by`."""
    run_dir = os.path.abspath(run_dir)
    entry = find_notebook_entry(run_dir, entry_id)
    if entry.get("type") not in ("error", "decision"):
        raise EvalFormatError("mục sổ '%s' type='%s' không phải error/decision"
                              % (entry_id, entry.get("type")))
    cases_dir = os.path.abspath(cases_dir or DEFAULT_CASES_DIR)
    os.makedirs(cases_dir, exist_ok=True)
    cid = _unique_case_id(cases_dir, case_id or ("incident-" + kg.slug(title or entry.get("title") or "case")))
    run_name = os.path.basename(run_dir)
    case = {
        "id": cid,
        "version": "v0.1",
        "source": "incident",
        "description": "Sinh từ sự cố: %s" % (title or entry.get("title")),
        "prompt": ("TODO: mô tả lại tình huống sự cố '%s' để agent làm lại và chấm.\n"
                   "Nguồn: sổ %s mục %s." % (title or entry.get("title"), run_name, entry_id)),
        "setup": {"note": "TODO: đặt bối cảnh/thư mục tạm, KHÔNG dùng run thật"},
        "trigger": ["skills/", "roles/", "AGENTS.md"],
        "expect": [],
        "_incident": {"run": run_name, "entry_id": entry_id, "entry_title": entry.get("title")},
        "_todo": "Điền 'expect' bằng scorer rule: must_call|must_not_call|must_mention|must_ask_before.",
    }
    case_path = os.path.join(cases_dir, cid + ".jsonl")
    with open(case_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")

    edge_msg = "chưa ghi KG (--no-kg)"
    if not no_kg:
        try:
            rel = kg.normalize_ref_path(os.path.relpath(case_path, root))
        except ValueError:
            rel = None
        if rel is None:
            edge_msg = "bỏ qua KG: case nằm ngoài gốc dự án %s" % root
        else:
            incident_id = ensure_incident_entity(run_dir, entry)
            art_id = kg.upsert_artifact_ref(run_dir, rel)
            kg.add_edge_checked(run_dir, incident_id, art_id, "evidenced_by",
                                recorded_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                source_ref=rel)
            edge_msg = "KG: %s --evidenced_by--> %s" % (incident_id, art_id)
    print("Đã sinh case khung: %s" % case_path)
    print("  %s" % edge_msg)
    print("  Việc cần làm: điền 'expect' rồi chạy --replay để kiểm chấm.")
    return cid


def cmd_add_incident(a):
    try:
        add_incident(a.run_dir or DEFAULT_RUN_DIR, a.title, a.from_notebook,
                     cases_dir=a.cases_dir, case_id=a.case_id, no_kg=a.no_kg,
                     root=os.path.abspath(a.root or ROOT))
    except (EvalFormatError, kg.KgError) as exc:
        print("LỖI: %s" % exc, file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _run_kwargs(p):
    p.add_argument("--root", help="gốc dự án để quét (mặc định repo này)")
    p.add_argument("--run-dir", help="thư mục run cho transcript/kết quả")
    p.add_argument("--cases-dir", help="thư mục case *.jsonl (mặc định evals/cases)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    p_run = sp.add_parser("run", help="chạy eval tĩnh / theo thay đổi / 1 case / replay / liệt kê")
    _run_kwargs(p_run)
    p_run.add_argument("--static", action="store_true", help="chạy tầng EVAL TĨNH")
    p_run.add_argument("--changed", action="store_true", help="suy eval từ git diff")
    p_run.add_argument("--base", help="REF gốc để git diff (mặc định HEAD)")
    p_run.add_argument("--list", action="store_true", help="liệt kê case hành vi")
    p_run.add_argument("--behavior", action="store_true", help="chạy 1 case trên agent thật")
    p_run.add_argument("--replay", metavar="TRANSCRIPT", help="chấm lại transcript offline")
    p_run.add_argument("--agent", default="claude", help="agent headless: orca id|claude|codex|command-code|pi")
    p_run.add_argument("--agent-cmd", help="lệnh agent tuỳ biến; '{}' sẽ được thay bằng prompt")
    p_run.add_argument("--case", help="id case (bắt buộc với --behavior)")
    p_run.add_argument("--timeout", type=int, default=600, help="timeout giây cho 1 case hành vi")

    p_add = sp.add_parser("add-incident", help="sinh case khung từ mục sổ (error/decision)")
    _run_kwargs(p_add)
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--from-notebook", required=True, dest="from_notebook", metavar="ENTRY_ID")
    p_add.add_argument("--case-id")
    p_add.add_argument("--no-kg", action="store_true", help="không ghi cạnh KG")

    a = ap.parse_args(argv)
    if a.cmd == "add-incident":
        return cmd_add_incident(a)

    root = os.path.abspath(a.root or ROOT)
    if a.static:
        return print_static(root)
    if a.changed:
        return cmd_changed(root, a.base)
    if a.behavior:
        if not a.case:
            print("LỖI: --behavior cần --case <ID> (chỉ chạy 1 case mỗi lần).", file=sys.stderr)
            return 1
        a.cases_dir = a.cases_dir or os.path.join(root, "evals", "cases")
        return cmd_behavior(a)
    if a.replay:
        a.cases_dir = a.cases_dir or os.path.join(root, "evals", "cases")
        return cmd_replay(a)
    # Mặc định: chỉ liệt kê (không tốn tiền, không chạy agent).
    return print_list(os.path.abspath(a.cases_dir or os.path.join(root, "evals", "cases")))


if __name__ == "__main__":
    sys.exit(main())
