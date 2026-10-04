#!/usr/bin/env python3
"""Turn plan.json (task DAG) into Orca orchestration commands.

Modes
  (default) / --dry-run   print waves + the exact orca commands, execute nothing
  --create                run-create (unless --run given or task_map.json already
                          holds _run from the seed plan — one Orca Run spans both
                          plans, _run is reused, never overwritten) + task-create
                          deps translated to real task ids; writes <run_dir>/task_map.json
  --start-ready           worker-start for every task whose deps are all in <run_dir>/done.json
                          and that is not started yet (state in <run_dir>/started.json)

Task kinds in plan.json: "worker" (agent does it) | "gate" (human decision; coordinator asks the
user, never started as a worker). A gate blocks every task that lists it in deps.
Every worker task becomes a self-contained spec: Target/Change/Constraints/Ownership/Acceptance.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys


def resolve_orca():
    """Same CLI rule as skills/ai-pipeline-orca/SKILL.md: ORCA_CLI_COMMAND,
    else orca-dev under ORCA_DEV_REPO_ROOT, else orca-ide on Linux
    (`orca` there collides with the GNOME screen reader), else orca."""
    if os.environ.get("ORCA_CLI_COMMAND"):
        return os.environ["ORCA_CLI_COMMAND"]
    if os.environ.get("ORCA_DEV_REPO_ROOT"):
        return "orca-dev"
    if sys.platform.startswith("linux"):
        return "orca-ide"
    return "orca"


ORCA = resolve_orca()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def require_orca():
    """Fail with a clear error (before any mutation) if the CLI is missing."""
    if shutil.which(ORCA) is None:
        sys.exit(f"plan error: orca CLI '{ORCA}' not found on PATH "
                 f"(set ORCA_CLI_COMMAND to override)")
    return ORCA


sys.stdout.reconfigure(encoding="utf-8")  # Windows pipes default to cp1252
sys.stderr.reconfigure(encoding="utf-8")

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import autonomy as autonomy_mod
except ImportError:  # stdlib-only, khong bao gio fail nang vi thieu policy
    autonomy_mod = None


BUILD_ROLES = {"module-dev", "integrator", "error-analyst", "weakness-diagnostician"}

MODES = ("train", "evaluate-only", "retrieve-only", "inference-service", "monitor")


def load_registry():
    """roles/registry.json là nguồn duy nhất cho role -> nhóm; thiếu file thì dùng fallback cứng."""
    try:
        with open(os.path.join(ROOT, "roles", "registry.json"), encoding="utf-8") as f:
            reg = json.load(f)
        groups = {r: g for g, rs in reg.get("groups", {}).items() for r in rs}
        if groups:
            return groups
    except (OSError, ValueError):
        pass
    return dict(GROUP_OF_FALLBACK)


GROUP_OF_FALLBACK = {"module-dev": "code", "integrator": "code", "error-analyst": "code", "feasibility-analyst": "code", "weakness-diagnostician": "code",
            "model-proposer": "debate", "critic": "debate", "architect": "debate"}


def group_of(role):
    return load_registry().get(role, "analysis")


def effective_mode(t):
    """Mode thực thi: trường `mode` mới; thiếu thì suy từ phase/id/role cũ (phase train/probe = train)."""
    if t.get("mode") in MODES:
        return t["mode"]
    return "train" if is_train_task(t) else None


def needs_g3(t):
    """Task can gate G3 (thong tin GPU/server) khi train HOAC can compute GPU.

    Moi gia tri compute thuoc nhom GPU trong schemas/plan.schema.json (hien chi
    co "gpu") deu can G3, ke ca mode evaluate-only/inference-service. CPU-only
    chay container thuong, khong can G3.
    """
    if effective_mode(t) == "train":
        return True
    return (t.get("resources") or {}).get("compute") == "gpu"


def is_train_task(t):
    if t.get("phase") in ("train", "probe"):  # probe = cheap baseline training, needs G2+G3 too
        return True
    # explicit `phase` is the contract; only fall back to the task id/role (not free-text titles,
    # which false-positive on e.g. "train/val/test split analysis")
    return any(w in t.get("id", "").lower() for w in ("train",)) or t.get("role") == "trainer"


def is_build_task(t):
    if t.get("mode") in MODES:
        return True
    # legacy (không có mode): giữ nguyên ngữ nghĩa cũ để plan cũ vẫn validate
    return t.get("phase") in ("train", "build") or is_train_task(t) or t.get("role") in BUILD_ROLES


def worktree_for(t):
    """Parallel build/train work gets its own worktree; read-only analysis stays on the current one."""
    if t.get("mode") == "monitor":  # quan sát/giám sát: đọc artifact, không cần checkout riêng
        return t.get("worktree") or "current"
    if t.get("mode") in MODES:
        return t.get("worktree") or "new-child"
    return t.get("worktree") or ("new-child" if is_build_task(t) else "current")


def in_git_repo():
    r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "true"


def require_git_for_worktrees(tasks):
    if any(worktree_for(t) in ("new-child", "new-top-level") for t in tasks if t.get("kind", "worker") == "worker")             and not in_git_repo():
        sys.exit("plan error: parallel build tasks need separate git worktrees but this folder is not a git repo. "
                 "Run: git init && git add -A && git commit -m init  (or set \"worktree\": \"current\" per task)")


GROUP_OF = GROUP_OF_FALLBACK


def load_roster(run_dir):
    p = os.path.join(run_dir, "agents.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def assign_agents(tasks, roster, skip=()):
    """Make every worker's agent come from the human-selected roster (scripts/agent_roster.py).
    task.agent absent/"auto" -> round-robin inside the role's group (code | debate | analysis); an explicit agent
    must be one the human selected for any group. Debate tasks of the same wave get different agents when possible."""
    g = roster["groups"]
    allowed = {x for v in g.values() for x in v}
    counters = {}
    for t in tasks:
        if t.get("kind", "worker") != "worker" or t["id"] in skip:  # already started: its agent is history
            continue
        group = group_of(t.get("role", ""))
        pool = g.get(group) or g["code"]
        if t.get("agent") in (None, "auto"):
            i = counters.get(group, 0)
            counters[group] = i + 1
            t["agent"] = pool[i % len(pool)]
        elif t["agent"] not in allowed:
            sys.exit(f"roster error: task {t['id']} wants agent '{t['agent']}' which the human did not select "
                     f"(selected: {', '.join(sorted(allowed))}). Edit the plan or re-run agent_roster.py select.")
        con = (roster.get("constraints") or {}).get(t["agent"], {})
        if con.get("worktree") and worktree_for(t) in ("new-child", "new-top-level") and not t.get("worktree"):
            t["worktree"] = con["worktree"]  # e.g. pi only starts reliably with --worktree current (see agents.json)
            print(f"note: {t['id']} runs on agent {t['agent']} with worktree={con['worktree']} (agent constraint: {con.get('evidence', 'agents.json')})")


def ancestors(tid, by_id):
    """Transitive dependency closure of task tid (symbolic plan ids)."""
    seen, stack = set(), list(by_id.get(tid, {}).get("deps", []))
    while stack:
        d = stack.pop()
        if d in seen or d not in by_id:
            continue
        seen.add(d)
        stack.extend(by_id[d].get("deps", []))
    return seen


def validate_plan(plan):
    """Semantic validation BEFORE any Orca mutation. Exits non-zero with a
    clear message (no traceback) on the first problem found."""
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        sys.exit("plan error: 'tasks' must be a non-empty list")
    by_id = {t["id"]: t for t in tasks if isinstance(t, dict) and "id" in t}
    ids = [t["id"] for t in plan["tasks"]]
    if len(ids) != len(set(ids)):
        sys.exit("plan error: duplicate task ids")
    for t in plan["tasks"]:
        for d in t.get("deps", []):
            if d not in ids:
                sys.exit(f"plan error: {t['id']} depends on unknown {d}")
    for t in plan["tasks"]:
        if t.get("kind", "worker") == "worker":
            if not t.get("change"):
                sys.exit(f"plan error: worker {t['id']} is missing required field 'change'")
            if not t.get("acceptance"):
                sys.exit(f"plan error: worker {t['id']} is missing required field 'acceptance'")
    waves(tasks)  # exits non-zero on dependency cycle
    for t in tasks:  # mode phải thuộc enum; resources.compute cpu|gpu
        if t.get("mode") is not None and t["mode"] not in MODES:
            sys.exit(f"plan error: task {t['id']} has unknown mode '{t['mode']}' (one of: {', '.join(MODES)})")
        comp = (t.get("resources") or {}).get("compute")
        if comp is not None and comp not in ("cpu", "gpu"):
            sys.exit(f"plan error: task {t['id']} resources.compute must be cpu|gpu, got '{comp}'")
    gates = {t["id"] for t in tasks if t.get("kind") == "gate"}
    trains = [t for t in tasks if t.get("kind", "worker") == "worker" and needs_g3(t)]
    builds = [t for t in tasks if t.get("kind", "worker") == "worker" and is_build_task(t)]
    if trains and "G3" not in gates:
        sys.exit("plan error: plan has train/GPU task(s) (mode=train hoac resources.compute=gpu) "
                 "but no gate G3 (thong tin GPU/server can truoc khi dung GPU)")
    if builds and "G2" not in gates:
        sys.exit("plan error: plan has build/train task(s) but no gate G2 (plan approval required)")
    for t in trains:
        if "G3" not in ancestors(t["id"], by_id):
            mode = effective_mode(t) or "-"
            comp = (t.get("resources") or {}).get("compute", "-")
            sys.exit(f"plan error: task {t['id']} (mode={mode}, compute={comp}) can GPU/server: "
                     f"phai co duong phu thuoc toi gate G3")
    for t in builds:
        if "G2" not in ancestors(t["id"], by_id):
            sys.exit(f"plan error: build/train task {t['id']} must have a dependency path to gate G2")
    return plan


def load_plan(path):
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    return validate_plan(plan)


def waves(tasks):
    """Kahn layering. Returns list of lists of task dicts; exits on cycle."""
    remaining = {t["id"]: t for t in tasks}
    done, out = set(), []
    while remaining:
        layer = [t for t in remaining.values() if set(t.get("deps", [])) <= done]
        if not layer:
            sys.exit("plan error: dependency cycle among " + ", ".join(remaining))
        out.append(layer)
        for t in layer:
            done.add(t["id"])
            del remaining[t["id"]]
    return out


def build_spec(plan, t, run_dir):
    role = t.get("role", "module-dev")
    run_dir = os.path.abspath(run_dir)  # shared across worktrees: artifacts/reports go here, not into the worktree
    isolated = worktree_for(t) in ("new-child", "new-top-level")
    mode_line = ""
    if t.get("mode"):
        res = t.get("resources") or {}
        bits = [t["mode"], "compute=" + res.get("compute", "cpu")]
        if res.get("container"):
            bits.append("container=" + res["container"])
        if needs_g3(t):
            bits.append("cần G3 (GPU/server)")
        else:
            bits.append("không cần G3/GPU")
        mode_line = "Mode: " + ", ".join(bits)
    lang = plan.get("report_lang", "vi")
    lines = [
        f"TASK {t['id']}: {t['title']}",
        f"Role prompt: read roles/{role}.md in the project root and follow it exactly.",
        f"Run dir: {run_dir}  (spec: {plan.get('spec', 'spec.md')})",
        f"Target: {t.get('target', 'see inputs')}",
        *([mode_line] if mode_line else []),
        f"Change: {t['change']}",
        "Constraints: " + "; ".join(t.get("constraints", []) + [
            "all dataset/model artifacts must carry a version tag",
            f"reports for the user are written in {'Vietnamese' if lang == 'vi' else 'English'} (run report_lang={lang})",
            "ask the coordinator (orca orchestration ask) instead of guessing when blocked on a human decision",
            f"log each experiment/finding to the problem notebook: python scripts/notebook.py log {run_dir} --type experiment|research|error|insight --title ... --body ... --author {role} (hypothesis, setup, metrics, conclusion; see skills/ai-pipeline-notebook)",
        ]),
        "Ownership: you may edit only " + ", ".join(t.get("owns", [f"{run_dir}/artifacts/{t['id']}/"])),
        "Inputs: " + (", ".join(t.get("inputs", [])) or "none"),
        "Outputs: " + (", ".join(t.get("outputs", [])) or f"{run_dir}/artifacts/{t['id']}/result.md"),
        "Observable acceptance: " + t["acceptance"],
        *(["Worktree: you run in your OWN git worktree/branch. Commit code changes there (small commits); write artifacts, "
           "eval.json and reports to the absolute run dir above so others can read them. Never edit another task's paths; "
           "the integrator merges branches."] if isolated else []),
        "Finish with worker_done (outcome succeeded|failed) and --report-path pointing at your main output.",
    ]
    return "\n".join(lines)


def cmd_str(argv):
    return " ".join(shlex.quote(a) for a in argv)


def run(argv):
    r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"command failed: {cmd_str(argv)}\n{r.stdout}\n{r.stderr}")
    return json.loads(r.stdout) if r.stdout.strip().startswith(("{", "[")) else r.stdout


def find_id(obj, keys=("id",)):
    """First id inside the receipt's `result` (top-level `id` is only the request id)."""
    if isinstance(obj, dict) and "ok" in obj and "result" in obj:
        obj = obj["result"]
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), str):
                return obj[k]
        for v in obj.values():
            r = find_id(v, keys)
            if r:
                return r
    return None


def rd(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return default


def wr(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def worker_argv(t, task_id, default_agent, run_name="run"):
    wt = worktree_for(t)
    argv = [ORCA, "orchestration", "worker-start", "--task", task_id, "--worktree", wt]
    if wt in ("new-child", "new-top-level"):
        argv += ["--name", f"{run_name}-{t['id'].lower()}"]
    argv += ["--agent", t.get("agent", default_agent), "--json"]
    if t.get("model"):
        argv += ["--model", t["model"]]
        if t.get("effort"):
            argv += ["--effort", t["effort"]]
    return argv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--run-dir", help="default: runs/<plan.run_id>")
    ap.add_argument("--run", help="existing Orca run id (skip run-create)")
    ap.add_argument("--agent", default="claude", help="default agent for tasks without one (ignored when agents.json exists)")
    ap.add_argument("--no-roster", action="store_true", help="skip the agent roster gate (documented exception only)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--create", action="store_true")
    g.add_argument("--start-ready", action="store_true")
    a = ap.parse_args()

    plan = load_plan(a.plan)
    run_dir = a.run_dir or os.path.join("runs", plan.get("run_id", "run"))
    tasks = plan["tasks"]
    workers = [t for t in tasks if t.get("kind", "worker") == "worker"]
    roster = load_roster(run_dir)
    if roster:
        st = rd(os.path.join(run_dir, "started.json"), {})
        assign_agents(tasks, roster, skip=set(st))
    elif (a.create or a.start_ready) and not a.no_roster:
        sys.exit("agent roster missing: run `python scripts/agent_roster.py detect`, ask the human to pick the orchestrator and the "
                 f"code/debate agents, then `python scripts/agent_roster.py select {run_dir} --orchestrator .. --code .. --debate ..` "
                 "(skills/ai-pipeline-agents). Use --no-roster only for a documented exception.")

    if a.create:
        require_orca()
        require_git_for_worktrees(tasks)
        state_path = os.path.join(run_dir, "task_map.json")
        tmap = rd(state_path, {})
        # One Orca Run spans the seed plan and the later build plan: reuse the
        # stored _run, never overwrite it, and keep task ids consistent.
        if tmap.get("_run"):
            if a.run and a.run != tmap["_run"]:
                sys.exit(f"plan error: --run {a.run} conflicts with stored run "
                         f"{tmap['_run']} in {state_path} (one Run per run dir)")
            run_id = tmap["_run"]
        elif a.run:
            run_id = tmap["_run"] = a.run
        else:
            out = run([ORCA, "orchestration", "run-create", "--objective", plan.get("objective", plan["title"]), "--json"])
            run_id = tmap["_run"] = find_id(out)
            wr(state_path, tmap)
        # gates are not Orca tasks; a task depending on a gate gets no Orca dep for it (coordinator holds start).
        for layer in waves(tasks):
            for t in layer:
                if t.get("kind", "worker") != "worker" or t["id"] in tmap:
                    continue
                deps = [tmap[d] for d in t.get("deps", []) if d in tmap]
                argv = [ORCA, "orchestration", "task-create", "--spec", build_spec(plan, t, run_dir),
                        "--task-title", t["title"], "--run", run_id, "--json"]
                if deps:
                    argv += ["--deps", json.dumps(deps)]
                tmap[t["id"]] = find_id(run(argv))
                wr(state_path, tmap)
        print(f"created {len(tmap) - 1} tasks in run {tmap['_run']} -> {state_path}")
        return

    if a.start_ready:
        require_orca()
        require_git_for_worktrees(tasks)
        tmap = rd(os.path.join(run_dir, "task_map.json"), {})
        done = set(rd(os.path.join(run_dir, "done.json"), []))
        started_raw = rd(os.path.join(run_dir, "started.json"), {})
        started = dict(started_raw) if isinstance(started_raw, dict) else {tid: True for tid in started_raw}
        started_path = os.path.join(run_dir, "started.json")

        def save_started():
            wr(started_path, started)

        todo = [t for t in tasks if t["id"] not in started and set(t.get("deps", [])) <= done]
        if not todo:
            print("nothing ready (mark finished tasks/gates in done.json)")
            return
        # Human-on-the-loop: kiem tra autonomy policy truoc khi start (T3).
        # Khong co policy file -> cho phep (tuong thich nguoc run cu), in ghi chu ro.
        # Policy hong -> canh bao ro va van chay (tuong thich nguoc); sua policy
        # truoc khi siet cap (`autonomy.py validate` de kiem tra chat).
        policy = autonomy_mod.load_policy(run_dir) if autonomy_mod else None
        policy_errs = autonomy_mod.validate_policy(policy) if (autonomy_mod and policy is not None) else []
        if policy is None:
            print("note: khong co autonomy_policy.json: cho phep start (tuong thich nguoc run cu). "
                  "Copy templates/autonomy_policy.template.json de bat cap/autonomy.")
        elif policy_errs:
            print("canh bao policy: autonomy_policy.json KHONG hop le: " + "; ".join(policy_errs) +
                  " -- van cho phep start (tuong thich nguoc). Sua policy roi chay lai de bat cap.")
            policy = None
        usage = autonomy_mod.read_usage(run_dir) if autonomy_mod else {}
        # Cap quota theo TUNG worker truoc khi start: worker duoc duyet cong
        # tasks_started ngay vao usage.json (atomic), vong check ke tiep trong
        # cung wave thay so lieu moi. Worker vuot cap -> audit start_denied.
        allowed_ids, blocked = set(), []
        if policy is not None:
            for t in todo:
                if t.get("kind", "worker") != "worker":
                    continue
                ok, reason, warns = autonomy_mod.check_action(
                    policy, "start", autonomy_mod.phase_of_task(t), usage)
                for w in warns:
                    print(f"canh bao policy ({t['id']}): {w}")
                if not ok:
                    blocked.append((t["id"], reason))
                    autonomy_mod.append_audit(run_dir, "start_denied", scope=t["id"],
                                              decision="denied", reason=reason)
                else:
                    usage = autonomy_mod.reserve_task_quota(run_dir, 1, usage=usage)
                    allowed_ids.add(t["id"])
        else:
            allowed_ids = {t["id"] for t in todo if t.get("kind", "worker") == "worker"}
        for t in todo:
            if t.get("kind", "worker") == "gate" and t["id"] in done:
                started[t["id"]] = True  # gate already answered: nothing to ask
                save_started()
                continue
            if t.get("kind", "worker") == "gate":
                print(f"GATE {t['id']} ready — ask the human: {t['title']}  (then add '{t['id']}' to done.json)")
                started[t["id"]] = True
                save_started()  # persist immediately so a later failure cannot lose it
                continue
            if t["id"] not in tmap:
                sys.exit(f"plan error: {t['id']} has no Orca task id in task_map.json (run --create first)")
            if t["id"] not in allowed_ids:
                continue  # vuot cap: da ghi audit start_denied o vong admission, bao exit o cuoi
            receipt = run(worker_argv(t, tmap[t["id"]], a.agent, plan["run_id"]) + ["--run", tmap["_run"]])
            if isinstance(receipt, dict) and receipt.get("ok") is False:
                sys.exit(f"worker start failed for {t['id']}: {json.dumps(receipt, ensure_ascii=False)}")
            detail = receipt.get("result", {}) if isinstance(receipt, dict) else {}
            if isinstance(detail, dict) and (detail.get("failedStage") or detail.get("residualResources")):
                sys.exit(f"worker start reported failure for {t['id']}: {json.dumps(detail, ensure_ascii=False)}")
            started[t["id"]] = (detail.get("dispatchId") if isinstance(detail, dict) else None) or find_id(receipt) or True  # dispatch id for retry checks
            save_started()  # persist after EACH receipt so a retry never double-starts a worker
            if autonomy_mod and policy is not None:
                autonomy_mod.append_audit(run_dir, "start", scope=t["id"], decision="started",
                                          reason=f"agent={t.get('agent', a.agent)} policy={policy.get('policy_version')}",
                                          refs=[tmap.get("_run", "")])
            print(f"started {t['id']} ({t.get('agent', a.agent)})")
        if blocked:
            for tid, reason in blocked:
                print(f"TREO start {tid}: policy cam -- {reason}")
            sys.exit("policy: tu choi start %d task (%s). Can nguoi approve/doi policy "
                     "(autonomy.py approve) roi chay lai --start-ready."
                     % (len(blocked), ", ".join(t for t, _ in blocked)))
        return

    # dry run (default): DAG description only — NOT directly runnable.
    # <RUN_ID>/<TASK_ID> are placeholders resolved at --create; --deps shows
    # symbolic plan ids mapped to real Orca task ids by --create.
    print(f"# {plan['title']}  | run dir: {run_dir}")
    print(f"# CLI: {ORCA}")
    print("# DRY-RUN: DAG description only, not runnable as shown. Use --create to build it.\n")
    print(cmd_str([ORCA, "orchestration", "run-create", "--objective", plan.get("objective", plan["title"]), "--json"]))
    for i, layer in enumerate(waves(tasks), 1):
        print(f"\n=== WAVE {i} (parallel: {sum(1 for t in layer if t.get('kind','worker')=='worker')} workers) ===")
        for t in layer:
            kind = t.get("kind", "worker")
            deps = ",".join(t.get("deps", [])) or "-"
            if kind == "gate":
                print(f"[GATE {t['id']}] {t['title']}  deps={deps}  -> coordinator asks the human, no worker")
                continue
            print(f"[{t['id']}] {t['title']}  role={t.get('role','module-dev')} agent={t.get('agent', a.agent)} deps={deps}"
                  + (f" mode={t['mode']}" if t.get("mode") else "")
                  + (f" resources={t['resources'].get('compute', '?')}" if t.get("resources") else ""))
            print("   " + cmd_str([ORCA, "orchestration", "task-create", "--task-title", t["title"],
                                   "--spec", "<spec: %d chars, passed via --create>" % len(build_spec(plan, t, run_dir)),
                                   "--run", "<RUN_ID>",
                                   "--deps", json.dumps(t.get("deps", []))]) + "  # deps are symbolic plan ids")
            print("   " + cmd_str(worker_argv(t, "<TASK_ID>", a.agent, plan["run_id"])))
    print(f"\n# then: {ORCA} orchestration check --wait --types worker_done,escalation,question --timeout-ms 900000 --json")
    print(f"# finally: {ORCA} orchestration worker-list --terminal-state reclaimable --json  (release/retain each)")


if __name__ == "__main__":
    main()
