#!/usr/bin/env python3
"""Detect where a pipeline run stands and what to execute next (evidence = files in the run dir).

Usage: project_status.py [run_dir] [--json]     (default run_dir: newest folder under runs/)

Phases: 0 intake · 1 analysis · 2 planning · 3 build · 4 integration · 5 optimize · 6 release.
The script reads artifacts only; it does not know whether a worker is still running. The
status-assessor agent adds that (orca worker-list / task-list) and judges artifact quality.
"""
import argparse
import glob
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_OPT_ROUNDS = 3
MODULE_MODES = ("train", "evaluate-only", "retrieve-only", "inference-service")


def task_mode(t):
    if t.get("mode") in MODULE_MODES + ("monitor",):
        return t["mode"]
    if t.get("phase") in ("train", "probe"):  # legacy: phase train/probe = train
        return "train"
    if any(w in t.get("id", "").lower() for w in ("train",)) or t.get("role") == "trainer":
        return "train"
    return None


try:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from plan_to_orca import needs_g3 as _plan_needs_g3
except ImportError:  # chay doc lap: fallback cung ngu nghia
    _plan_needs_g3 = None


def task_needs_g3(t):
    """Task co can gate G3 (GPU/server) khong: dung chung helper voi plan_to_orca.

    Can G3 khi mode=train HOAC resources.compute=gpu (ke ca evaluate-only tren
    GPU). CPU-only khong can G3.
    """
    if _plan_needs_g3 is not None:
        return bool(_plan_needs_g3(t))
    if task_mode(t) == "train":
        return True
    return (t.get("resources") or {}).get("compute") == "gpu"


def newest_run():
    runs = [d for d in glob.glob(os.path.join(ROOT, "runs", "*")) if os.path.isdir(d)]
    return max(runs, key=os.path.getmtime) if runs else None


def jload(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def find(run_dir, *names):
    for n in names:
        hits = glob.glob(os.path.join(run_dir, "**", n), recursive=True)
        if hits:
            return hits[0]
    return None


def autonomy_summary(run_dir):
    """T3: tom tat policy/budget/canh bao. None neu run chua co policy."""
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import autonomy as au
    except ImportError:
        return None
    pol = au.load_policy(run_dir)
    if pol is None:
        return None
    if au.validate_policy(pol):
        return {"policy_version": pol.get("policy_version", "?"), "default_mode": pol.get("default_mode"),
                "usage": {}, "warnings": ["policy khong hop le: " + "; ".join(au.validate_policy(pol))],
                "blocked": "policy khong hop le, sua autonomy_policy.json"}
    usage = au.read_usage(run_dir)
    caps, warn_at = pol.get("caps") or {}, pol.get("warn_at", 0.8)
    frac, warnings, blocked = {}, [], ""
    for cap_key in au.CAP_KEYS:
        cap = caps.get(cap_key)
        if cap is None:
            continue
        used = usage.get(au.USAGE_OF[cap_key], 0) or 0
        frac[cap_key] = round(used / cap, 3) if cap else 0
        if used >= cap:
            blocked = f"vuot tran {cap_key} ({used}/{cap}): can nguoi duyet truoc khi start tiep"
        elif used >= warn_at * cap:
            warnings.append(f"gan tran {cap_key}: {used}/{cap}")
    return {"policy_version": pol.get("policy_version"), "default_mode": pol.get("default_mode"),
            "usage": frac, "warnings": warnings, "blocked": blocked}


def round_docs_gap(run_dir):
    """Round chua du 3 tai lieu (data/method/results_report.html); chi khi optimize_policy bat require_round_docs.

    Tra (bat, {ten_round: [van de]}). Policy cu thieu khoa/khong hop le = tat (hanh vi cu).
    """
    pol = jload(os.path.join(run_dir, "optimize_policy.json"), None)
    if not isinstance(pol, dict) or pol.get("require_round_docs") is not True:
        return False, {}
    try:
        import round_docs
    except ImportError:
        return True, {}
    gap = {}
    for d in sorted(glob.glob(os.path.join(run_dir, "reports", "round-*"))):
        if os.path.isdir(d):
            problems = round_docs.check(d)
            if problems:
                gap[os.path.basename(d)] = problems
    return True, gap


def spec_ok(spec):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "validate_spec.py"), spec, "--json"],
                       capture_output=True, text=True, encoding="utf-8")
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"ok": False, "questions": [{"key": "spec", "question": "validate_spec failed: " + r.stderr[:200]}]}


def assess(run_dir):
    rel = lambda p: os.path.relpath(p, run_dir) if p else None
    done = set(jload(os.path.join(run_dir, "done.json"), []))
    started = jload(os.path.join(run_dir, "started.json"), {})
    started = set(started if isinstance(started, (dict, list)) else [])
    spec = os.path.join(run_dir, "spec.md")
    ev = {}  # evidence per checkpoint

    sp = spec_ok(spec) if os.path.exists(spec) else {"ok": False, "questions": [{"key": "spec", "question": "spec.md chưa có trong run dir"}]}
    ev["spec_complete"] = sp["ok"]
    ev["G1"] = "G1" in done
    for k, names in {
        "data_analysis": ["data_analysis.md"], "requirements": ["requirements.md"], "research": ["research.md"],
        "proposals": ["proposal.md"], "critique": ["critique.md"], "architecture": ["architecture.md"],
    }.items():
        ev[k] = bool(find(run_dir, *names))
    # a run may hold a seed plan (run root) and the architect's build plan (artifacts/*/plan.json): use the one with build tasks
    # (legacy role check giữ nguyên để tương thích; cộng thêm task có execution mode mới)
    plans = [p for p in glob.glob(os.path.join(run_dir, "**", "plan.json"), recursive=True)
             if any(t.get("kind", "worker") == "worker" and (t.get("mode") in MODULE_MODES + ("monitor",)
                    or t.get("role") in ("module-dev", "integrator"))
                    for t in jload(p, {}).get("tasks", []))]
    plan = plans[0] if plans else None
    ev["build_plan"] = bool(plan)
    ev["G2"], ev["G3"] = "G2" in done, "G3" in done
    plan_tasks = jload(plan, {}).get("tasks", []) if plan else []
    ev["needs_g3"] = any(t.get("kind", "worker") == "worker" and task_needs_g3(t) for t in plan_tasks)
    ev["has_train"] = ev["needs_g3"]  # alias cu (tuong thich nguoc): that ra la needs_g3
    ev["report_lang"] = jload(plan, {}).get("report_lang", "vi") if plan else "vi"

    mods = sorted(d for d in glob.glob(os.path.join(run_dir, "modules", "*")) if os.path.isdir(d))
    mod_state = {os.path.basename(m): all(os.path.exists(os.path.join(m, f)) for f in ("eval.json", "report.md", "report.html"))
                 for m in mods}
    planned_modules = []
    if plan:
        planned_modules = [os.path.basename(o.rstrip("/\\")) for t in jload(plan, {}).get("tasks", [])
                           if t.get("role") == "module-dev" or t.get("mode") in MODULE_MODES
                           for o in t.get("owns", [])[:1]]
    e2e = find(run_dir, os.path.join("e2e", "eval.json")) or next(iter(glob.glob(os.path.join(run_dir, "artifacts", "I*", "eval.json"))), None)
    ev["e2e_eval"] = bool(e2e)
    ev["e2e_reports"] = bool(e2e) and all(os.path.exists(os.path.join(os.path.dirname(e2e), f)) for f in ("report.md", "report.html"))
    rounds = len(glob.glob(os.path.join(run_dir, "artifacts", "opt-*")))
    ev["release"] = os.path.isdir(os.path.join(run_dir, "release"))
    for k in ("modules/error",):  # error-analyst module reports count as e2e reports
        if not ev["e2e_reports"] and all(os.path.exists(os.path.join(run_dir, k, f)) for f in ("eval.json", "report.md", "report.html")):
            ev["e2e_reports"] = ev["e2e_eval"] = True

    nb_dir = os.path.join(run_dir, "notebook")
    nb = jload(os.path.join(nb_dir, "notebooklm.json"), {})
    jl = os.path.join(nb_dir, "journal.jsonl")
    ev["notebook"] = {"exists": os.path.isdir(nb_dir),
                      "entries": sum(1 for _ in open(jl, encoding="utf-8")) if os.path.exists(jl) else 0,
                      "notebooklm_url_set": bool(nb.get("notebook_url")), "last_export": nb.get("last_export") or None}
    ceil = jload(os.path.join(run_dir, "ceiling.json"), None)
    ev["ceiling"] = bool(ceil)
    ceil_block = None
    if ceil and ceil.get("estimates") and ceil.get("decision") not in ("retarget", "proceed", "stop"):
        up = ceil["estimates"][-1].get("upper")
        if up is not None and ceil.get("target") is not None and up < ceil["target"]:
            ceil_block = (f"Ceiling ({ceil['estimates'][-1].get('checkpoint')}) upper={up} < target={ceil['target']} "
                          "→ hỏi người: hạ mục tiêu / thêm data / đổi hướng / chấp nhận (ghi decision vào ceiling.json)")
    actions, blocked = [], []
    if not (ev["spec_complete"] and ev["G1"]):
        phase = 0
        if not ev["spec_complete"]:
            actions += [f"Gate G1: hỏi người — {q['question']}" for q in sp["questions"]]
        else:
            actions.append("Ghi nhận G1 hoàn tất: thêm 'G1' vào done.json")
        blocked.append("G1")
    elif not (ev["data_analysis"] and ev["requirements"] and ev["research"]):
        phase = 1
        for k, t in (("data_analysis", "A1 data-analyst"), ("requirements", "A2 requirements-analyst"), ("research", "A3 researcher")):
            if not ev[k]:
                actions.append(f"Chạy worker {t} (song song, deps: G1)")
    elif not (ev["proposals"] and ev["critique"] and ev["architecture"] and ev["build_plan"] and ev["G2"]):
        phase = 2
        if not ev["proposals"]:
            actions.append("Chạy debate: P1 + P2 model-proposer song song (agent khác nhau)")
        elif not ev["critique"]:
            actions.append("Chạy P3 critic")
        elif not (ev["architecture"] and ev["build_plan"]):
            actions.append("Chạy P4 architect/judge → architecture.md + plan.json (build phase)")
        else:
            actions.append("Gate G2: trình plan + phương thức triển khai cho người duyệt")
            blocked.append("G2")
    elif not all(mod_state.get(m) for m in (planned_modules or mod_state)) or not mod_state:
        phase = 3
        if ev["needs_g3"] and not ev["G3"]:
            actions.append("Gate G3: xin thông tin GPU server / loại GPU / CUDA / framework trước khi train/dùng GPU")
            blocked.append("G3")
        elif not ev["needs_g3"]:
            actions.append("Plan không có task needs_g3 (mode train hoặc compute gpu): không cần G3, chạy trên CPU/container thường")
        for m in (planned_modules or list(mod_state)):
            if not mod_state.get(m):
                actions.append(f"Module '{m}' chưa đủ eval.json + report.md + report.html → chạy/tiếp tục module-dev (worktree riêng)")
    elif not ev["e2e_reports"]:
        phase = 4
        actions.append("Ghép pipeline + e2e (I1: merge branch module, đo từng field)" if not ev["e2e_eval"]
                       else "Chạy I2 error-analyst: phân cụm lỗi, xuất report.md + report.html")
    elif not ev["release"]:
        phase = 5
        actions.append(f"So eval e2e với chỉ tiêu trong requirements.md. Đạt → Release (release/). "
                       f"Chưa đạt → vòng tối ưu #{rounds + 1}/{MAX_OPT_ROUNDS} theo nhóm lỗi ưu tiên cao"
                       if rounds < MAX_OPT_ROUNDS else
                       f"Đã hết {MAX_OPT_ROUNDS} vòng tối ưu: báo người dùng, xin gia hạn hoặc điều chỉnh chỉ tiêu")
    else:
        phase = 6
        actions.append("Hoàn tất. Chỉ cần tổng kết báo cáo cuối cho người dùng.")

    req_docs, docs_gap = round_docs_gap(run_dir)
    ev["round_docs"] = {"required": req_docs, "incomplete": docs_gap}
    for rname, problems in docs_gap.items():
        actions.insert(0, f"Round '{rname}' chưa đủ 3 tài liệu round ({'; '.join(problems)}) → "
                          f"python scripts/round_docs.py init {os.path.join(run_dir, 'reports', rname)} rồi điền hết placeholder, "
                          f"kiểm bằng round_docs.py check (policy require_round_docs)")
        if "round_docs" not in blocked:
            blocked.append("round_docs")

    if ceil_block:
        actions.insert(0, ceil_block)
        blocked.append("ceiling")
    roster = jload(os.path.join(run_dir, "agents.json"), None)
    ev["agents"] = roster and {"orchestrator": roster.get("orchestrator"), **roster.get("groups", {})}
    if not roster:
        actions.append("Chưa chọn agent: python scripts/agent_roster.py detect, hỏi người dùng, rồi select (skill ai-pipeline-agents)")
    ev["autonomy"] = autonomy_summary(run_dir)  # T3: policy/budget/canh bao human-on-the-loop
    if ev["autonomy"] is None:
        actions.append("Chưa có autonomy policy: copy templates/autonomy_policy.template.json thành runs/<id>/autonomy_policy.json, duyệt ở G2 (skill ai-pipeline-autonomy)")
    else:
        for w in ev["autonomy"]["warnings"]:
            actions.insert(0, "Autonomy: " + w)
        if ev["autonomy"]["blocked"]:
            blocked.append("autonomy")
            actions.insert(0, "Autonomy: " + ev["autonomy"]["blocked"])
    gj = os.path.join(ROOT, "graphify-out", "graph.json")
    ev["graphify"] = {"exists": os.path.exists(gj),
                      "age_days": round((__import__("time").time() - os.path.getmtime(gj)) / 86400, 1) if os.path.exists(gj) else None}
    if not ev["graphify"]["exists"]:
        actions.append("Chưa có knowledge graph dự án (Graphify): làm theo skill ai-pipeline-graph (hỏi duyệt cài trước)")
    if not ev["notebook"]["exists"]:
        actions.append("Chưa có sổ thí nghiệm: python scripts/notebook.py init " + run_dir)
    return {
        "run_dir": run_dir, "phase": phase,
        "phase_name": ["intake", "analysis", "planning", "build", "integration", "optimize", "release"][phase],
        "evidence": ev, "modules": mod_state, "opt_rounds": rounds,
        "gates_done": sorted(g for g in done if g.startswith("G")),
        "tasks_started": sorted(started), "tasks_done": sorted(done),
        "blocked_on_human": blocked, "next_actions": actions,
        "caveat": "Dựa trên file artifact; chưa biết worker nào đang chạy hay chất lượng artifact — assessor kiểm bằng orca + đọc nội dung.",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", nargs="?")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    run_dir = a.run_dir or newest_run()
    if not run_dir or not os.path.isdir(run_dir):
        msg = {"phase": None, "next_actions": ["Chưa có run nào: chạy skill ai-pipeline với 1 spec file để bắt đầu (phase 0 intake)."]}
        print(json.dumps(msg, ensure_ascii=False, indent=2) if a.json else msg["next_actions"][0])
        return 1
    res = assess(run_dir)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"Run: {run_dir}\nPhase {res['phase']} — {res['phase_name']}  | gates done: {', '.join(res['gates_done']) or '-'}")
        au = res["evidence"].get("autonomy")
        if au:
            use = ", ".join(f"{k}={v * 100:.0f}%" for k, v in au["usage"].items()) or "chua co usage"
            print(f"Autonomy: policy {au['policy_version']} ({au['default_mode']}) | budget: {use}")
        else:
            print("Autonomy: chua co policy")
        if res["modules"]:
            print("Modules: " + ", ".join(f"{m}={'OK' if ok else 'chưa xong'}" for m, ok in res["modules"].items()))
        print("Cần làm tiếp:")
        for x in res["next_actions"]:
            print("  -", x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
