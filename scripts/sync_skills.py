#!/usr/bin/env python3
"""Copy skills/ (single source of truth) into .claude/skills and .agents/skills.

Copies, not symlinks, so it works on Windows. Run after editing anything under skills/.
Only the skill folders present in skills/ are replaced; other skills a project keeps in
.claude/skills or .agents/skills are left alone.
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
    names = sorted(n for n in os.listdir(SRC) if os.path.isdir(os.path.join(SRC, n)))
    for t in TARGETS:
        rel = os.path.relpath(t, ROOT)
        if check:
            for n in names:
                if not in_sync(os.path.join(SRC, n), os.path.join(t, n)):
                    print(f"OUT OF SYNC: {os.path.join(rel, n)}")
                    bad = True
            continue
        os.makedirs(t, exist_ok=True)
        for n in names:
            dst = os.path.join(t, n)
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(os.path.join(SRC, n), dst)
        print(f"synced -> {rel}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
