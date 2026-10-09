#!/usr/bin/env python3
"""Run a plan task on an agent that Herdr cannot classify (today: command-code) in headless mode.

Usage: run_headless_agent.py <run_dir> <task_id> [--plan PLAN.json] [--agent command-code] [--max-turns 80]

Builds the self-contained task spec (plan_to_herdr.build_spec) plus a footer, writes it to <run_dir>/artifacts/<task>/prompt.txt, and starts
`command-code -p <prompt> --trust --no-session --accept-edits --max-turns N` in a visible Herdr pane (current checkout).
There is NO Herdr agent lifecycle for this agent: completion = the DONE.md the agent writes last (path printed below) and the files it owns; the
coordinator must verify them (tests, diff, ownership) exactly like a worker_done. Mark the task started in started.json yourself, or Herdr
`worker-start` will try (and fail at agent_readiness). Stdlib only.
"""
import argparse
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plan_to_herdr as p  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("task_id")
    ap.add_argument("--plan")
    ap.add_argument("--agent", default="command-code")
    ap.add_argument("--max-turns", type=int, default=80)
    a = ap.parse_args()
    if a.agent != "command-code":
        sys.exit("only command-code has a headless recipe here")
    run_dir = os.path.abspath(a.run_dir)
    plan = json.load(open(a.plan or os.path.join(run_dir, "plan.json"), encoding="utf-8"))
    task = next(t for t in plan["tasks"] if t["id"] == a.task_id)
    out = os.path.join(run_dir, "artifacts", a.task_id)
    os.makedirs(out, exist_ok=True)
    done = os.path.join(out, "DONE.md")
    spec = p.build_spec(plan, task, run_dir) + (
        "\n\nBạn chạy headless (không có người trả lời). Làm đủ rồi dừng. Nếu không có công cụ shell để chạy test thì chỉ viết code và test, "
        f"coordinator sẽ chạy `python -m unittest discover -s tests -v`. Việc CUỐI CÙNG: ghi file {done} gồm 3 câu tóm tắt (đã làm gì, kết quả test nếu có, còn lại gì). "
        "Không sửa file ngoài Ownership. Không cài thư viện. Không commit.")
    pf = os.path.join(out, "prompt.txt")
    open(pf, "w", encoding="utf-8").write(spec)
    log = os.path.join(out, "headless.log")
    if os.name == "nt":  # PowerShell
        cmd = (f"command-code -p (Get-Content -Raw -Encoding UTF8 '{pf}') --trust --no-session --accept-edits "
               f"--max-turns {a.max_turns} *> '{log}'")
    else:  # macOS / Linux (sh)
        cmd = (f"command-code -p \"$(cat '{pf}')\" --trust --no-session --accept-edits "
               f"--max-turns {a.max_turns} > '{log}' 2>&1")
    import herdr_rt
    herdr_rt.require_herdr()
    rc, out, err = herdr_rt.herdr(*herdr_rt.HERDR_CMDS["tab_create"], "--label", f"{a.task_id}-command-code-headless", "--cwd", os.getcwd(), "--no-focus")
    pane = herdr_rt.find_id(herdr_rt._json(out), ("pane_id", "root_pane_id")) if rc == 0 else None
    if not pane:
        print("herdr tab create failed:", (err or out)[:300])
    else:
        rc, out, err = herdr_rt.herdr(*herdr_rt.HERDR_CMDS["pane_run"], pane, cmd)
        print("pane:", pane if rc == 0 else (err or out)[:300])
    print(f"prompt: {pf} ({len(spec)} chars)\ncompletion marker: {done}\nlog: {log}")


if __name__ == "__main__":
    main()
