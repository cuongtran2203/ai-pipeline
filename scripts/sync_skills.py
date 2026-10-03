#!/usr/bin/env python3
"""Copy skills/ (single source of truth) into .claude/skills and .agents/skills.

Copies, not symlinks, so it works on Windows. Run after editing anything under skills/.
`--check` exits 1 if a target is out of sync (CI / pre-run guard).
"""
import filecmp
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "skills")
TARGETS = [os.path.join(ROOT, ".claude", "skills"), os.path.join(ROOT, ".agents", "skills")]


def in_sync(a, b):
    if not os.path.isdir(b):
        return False
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    return all(in_sync(os.path.join(a, d), os.path.join(b, d)) for d in cmp.common_dirs)


def main():
    check = "--check" in sys.argv
    bad = False
    for t in TARGETS:
        if check:
            if not in_sync(SRC, t):
                print(f"OUT OF SYNC: {os.path.relpath(t, ROOT)}")
                bad = True
            continue
        if os.path.isdir(t):
            shutil.rmtree(t)
        shutil.copytree(SRC, t)
        print(f"synced -> {os.path.relpath(t, ROOT)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
