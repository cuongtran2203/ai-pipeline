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
    plan = find(run_dir, "plan.json")
    ev["build_plan"] = bool(plan) and any(t.get("kind", "worker") == "worker" and t.get("role") in ("module-dev", "integrator")
                                          for t in jload(plan, {}).get("tasks", []))
    ev["G2"], ev["G3"] = "G2" in done, "G3" in done

    mods = sorted(d for d in glob.glob(os.path.join(run_dir, "modules", "*")) if os.path.isdir(d))
    mod_state = {os.path.basename(m): all(os.path.exists(os.path.join(m, f)) for f in ("eval.json", "report.md", "report.html"))
                 for m in mods}
    planned_modules = []
    if plan:
        planned_modules = [os.path.basename(o.rstrip("/\\")) for t in jload(plan, {}).get("tasks", [])
                           if t.get("role") == "module-dev" for o in t.get("owns", [])[:1]]
    e2e = find(run_dir, os.path.join("e2e", "eval.json")) or next(iter(glob.glob(os.path.join(run_dir, "artifacts", "I*", "eval.json"))), None)
    ev["e2e_eval"] = bool(e2e)
    ev["e2e_reports"] = bool(e2e) and all(os.path.exists(os.path.join(os.path.dirname(e2e), f)) for f in ("report.md", "report.html"))
    rounds = len(glob.glob(os.path.join(run_dir, "artifacts", "opt-*")))
    ev["release"] = os.path.isdir(os.path.join(run_dir, "release"))

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
        if not ev["G3"]:
            actions.append("Gate G3: xin thông tin GPU server / loại GPU / CUDA / framework trước khi train")
            blocked.append("G3")
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
        if res["modules"]:
            print("Modules: " + ", ".join(f"{m}={'OK' if ok else 'chưa xong'}" for m, ok in res["modules"].items()))
        print("Cần làm tiếp:")
        for x in res["next_actions"]:
            print("  -", x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
