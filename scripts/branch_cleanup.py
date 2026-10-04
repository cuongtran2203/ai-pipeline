#!/usr/bin/env python3
"""Close and delete worker branches/worktrees whose task is done AND already reviewed by the orchestrator.

  branch_cleanup.py <run_dir> [--branches b1,b2] [--apply] [--reviewed t1,t2]

Default = dry run: prints, per branch `<run_id>-<task_id>`, what would happen. `--apply` acts, but only on branches whose
task id is in done.json (the orchestrator verified worker_done + acceptance) and whose worktree has no uncommitted change.
`--reviewed` limits the apply to the task ids the orchestrator has just reviewed (recommended).

Per branch, in order (never destroys work):
  1. ahead of master = 0                      -> already merged: remove worktree + `git branch -d`.
  2. ahead > 0, every changed file is already on disk in this checkout with the same content (workers write the run dir
     directly) or only differs because the disk copy is newer -> work is captured: tag `archive/<branch>` at the tip
     (history stays reachable), remove worktree, delete branch.
  3. ahead > 0 with files that exist nowhere on disk: tracked, non-ignored new paths -> `git merge --no-ff` (aborted on conflict);
     ignored paths (runs/**) -> restored from the branch into the working dir; then step 2.
Never uses --force on `orca worktree rm`; refuses on a dirty worktree, a missing done.json entry, or a dirty master (for merges).
"""
import argparse
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORCA = os.environ.get("ORCA_CLI_COMMAND") or ("orca-dev" if os.environ.get("ORCA_DEV_REPO_ROOT") else "orca")


def git(*args, cwd=ROOT, check=False):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def worktrees():
    out, cur = {}, {}
    for line in git("worktree", "list", "--porcelain").stdout.splitlines() + [""]:
        if not line:
            if cur.get("branch"):
                out[cur["branch"].replace("refs/heads/", "")] = cur["worktree"]
            cur = {}
        elif " " in line:
            k, v = line.split(" ", 1)
            cur[k] = v
    return out


def file_state(branch, path):
    """same | newer-on-disk | missing | ignored-missing"""
    disk = os.path.join(ROOT, path)
    if not os.path.exists(disk):
        return "missing"
    blob = subprocess.run(["git", "show", f"{branch}:{path}"], cwd=ROOT, capture_output=True).stdout
    return "same" if open(disk, "rb").read() == blob else "differs"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--branches")
    ap.add_argument("--reviewed", help="comma list of task ids (e.g. PP,D1) the orchestrator has reviewed")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    run_id = os.path.basename(os.path.abspath(a.run_dir))
    done = set(json.load(open(os.path.join(a.run_dir, "done.json"), encoding="utf-8")))
    reviewed = set((a.reviewed or "").split(",")) - {""}
    wts = worktrees()
    wanted = set((a.branches or "").split(",")) - {""}
    branches = [b for b in wts if b.startswith(run_id + "-") and (not wanted or b in wanted)]
    master_clean = not git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    for b in sorted(branches):
        tid = b[len(run_id) + 1:].upper()
        wt = wts[b]
        ahead = int(git("rev-list", "--count", f"master..{b}").stdout.strip() or 0)
        dirty = bool(git("status", "--porcelain", cwd=wt).stdout.strip())
        why = []
        if tid not in done:
            why.append("task not in done.json")
        if reviewed and tid not in reviewed:
            why.append("not in --reviewed")
        if dirty:
            why.append("worktree has uncommitted changes")
        files = [f for f in git("diff", "--name-only", f"master...{b}").stdout.splitlines() if f]
        states = {f: file_state(b, f) for f in files} if ahead else {}
        in_master = {f: git("cat-file", "-e", f"master:{f}").returncode == 0 for f in states}
        # a file already tracked on master that the branch changes is NOT "captured on disk": it must be merged
        needs_merge_tracked = [f for f, st in states.items() if in_master[f] and st != "same"]
        missing = [f for f, st in states.items() if st == "missing" and not in_master[f]]
        ignored_missing = [f for f in missing if git("check-ignore", "-q", f).returncode == 0]
        plain_missing = [f for f in missing if f not in ignored_missing] + needs_merge_tracked
        differs = [f for f, st in states.items() if st == "differs" and not in_master[f]]
        plan = "delete (merged)" if not ahead else (
            f"{'merge ' if plain_missing else ''}{'restore ' + str(len(ignored_missing)) + ' ignored file(s) ' if ignored_missing else ''}"
            f"archive-tag + delete".strip())
        print(f"{b}: task {tid} ahead={ahead} files={len(files)} (missing={len(missing)}, differs={len(differs)}) "
              f"-> {'BLOCKED: ' + '; '.join(why) if why else plan}")
        if differs:
            print(f"    note: {len(differs)} file(s) on disk differ from the branch copy (disk is the newer working version): {', '.join(differs[:3])}")
        if why or not a.apply:
            continue
        if plain_missing:
            if not master_clean:
                print("    skip: master has uncommitted tracked changes; commit/stash before merging")
                continue
            r = git("merge", "--no-ff", "-m", f"Merge {b} (reviewed by orchestrator)", b)
            if r.returncode:
                git("merge", "--abort")
                print(f"    merge conflict -> aborted, branch kept: {r.stdout.strip()[:200]}")
                continue
            print("    merged into master")
        for f in ignored_missing:
            blob = subprocess.run(["git", "show", f"{b}:{f}"], cwd=ROOT, capture_output=True).stdout
            os.makedirs(os.path.dirname(os.path.join(ROOT, f)), exist_ok=True)
            open(os.path.join(ROOT, f), "wb").write(blob)
        if ahead and not plain_missing:
            git("tag", "-f", f"archive/{b}", b, check=True)
            print(f"    tagged archive/{b}")
        rm = subprocess.run([ORCA, "worktree", "rm", "--worktree", f"branch:{b}", "--json"], capture_output=True, text=True, encoding="utf-8")
        if '"ok": true' not in rm.stdout:
            print(f"    worktree rm failed, branch kept: {(rm.stdout or rm.stderr).strip()[:200]}")
            continue
        if not git("branch", "--list", b).stdout.strip():  # `orca worktree rm` already removed the branch
            print("    worktree removed, branch deleted")
            continue
        d = git("branch", "-d", b)
        if d.returncode:
            d = git("branch", "-D", b) if git("tag", "--list", f"archive/{b}").stdout.strip() else d
        print("    worktree removed, branch deleted" if d.returncode == 0 else f"    branch not deleted: {d.stderr.strip()[:160]}")
    if not branches:
        print("no worker branches for this run")


if __name__ == "__main__":
    main()
