"""ai-pipeline CLI: install the multi-agent workflow into any project (Linux, macOS, Windows).

Stdlib only. The framework files are bundled in ai_pipeline/payload (wheel) or read from the
repo root (editable/dev checkout).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__

MANIFEST = ".ai-pipeline.json"
BEGIN, END = "<!-- ai-pipeline:begin -->", "<!-- ai-pipeline:end -->"
COPY_DIRS = ["skills", "roles", "templates", "schemas", "scripts"]
GITIGNORE = ["runs/", "__pycache__/", "*.pyc", ".venv-graphify/", "graphify-out/", "vault/"]
ALIASES = {
    "status": "project_status.py", "validate": "validate_spec.py", "plan": "plan_to_orca.py",
    "report": "render_report.py", "notebook": "notebook.py", "kg": "kg.py",
    "agents": "agent_roster.py", "diagram": "playbook_diagram.py", "cleanup": "branch_cleanup.py",
    "autonomy": "autonomy.py", "supervisor": "supervisor.py", "seal": "seal.py",
    "vault": "obsidian_vault.py", "sync-skills": "sync_skills.py",
}


def payload_dir():
    here = Path(__file__).resolve().parent
    bundled = here / "payload"
    if (bundled / "skills").is_dir():
        return bundled
    root = here.parent
    if (root / "skills").is_dir() and (root / "AGENTS.md").is_file():
        return root
    sys.exit("ai-pipeline: khong tim thay payload (cai lai package: pip install --force-reinstall ai-pipeline)")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def iter_files(base):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in filenames:
            if not f.endswith(".pyc"):
                yield Path(dirpath) / f


def load_manifest(target):
    p = target / MANIFEST
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            sys.exit(f"ai-pipeline: {MANIFEST} bi hong, sua/xoa roi chay lai")
    return {"version": None, "files": {}}


def write_text(path, text, dry):
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def merge_block(path, block, dry):
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    wrapped = f"{BEGIN}\n{block.rstrip()}\n{END}\n"
    if BEGIN in text and END in text:
        new = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", lambda m: wrapped, text, flags=re.S)
    elif text:
        new = text.rstrip("\n") + "\n\n" + wrapped
    else:
        new = wrapped
    if new != text:
        write_text(path, new, dry)
        return True
    return False


def setup_gitignore(target, dry, log):
    gi = target / ".gitignore"
    have = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
    missing = [g for g in GITIGNORE if g not in have]
    if missing:
        body = "\n".join(have + ([""] if have and have[-1] else []) + ["# ai-pipeline"] + missing) + "\n"
        write_text(gi, body, dry)
        log(f"  updated   .gitignore (+{len(missing)} dong)")


class Log:
    def __init__(self, verbose):
        self.verbose = verbose
        self.count = {}
        self.warn = []

    def __call__(self, msg):
        print(msg)

    def note(self, action, rel):
        self.count[action] = self.count.get(action, 0) + 1
        if action != "add" or self.verbose:
            print(f"  {action:9} {rel}")


def place(src, dst, rel, manifest, force, update, dry, log):
    """Install one file without ever clobbering user content.

    add      : destination missing -> copy.
    same     : identical -> just record.
    update   : (update only) destination still equals what we installed -> refresh.
    conflict : (update only) user edited it -> keep theirs, write <file>.new.
    skip     : exists and differs -> keep theirs (init never overwrites; --force is opt-in, keeps .bak).
    """
    recorded = manifest["files"]
    new_hash = sha(src)
    if not dst.exists():
        action = "add"
    else:
        cur = sha(dst)
        if cur == new_hash:
            recorded.setdefault(rel, new_hash)
            return
        if force:
            action = "overwrite"
        elif update and recorded.get(rel) == cur:
            action = "update"
        elif update and rel in recorded:
            action = "conflict"
        else:
            action = "skip"
    if action in ("add", "overwrite", "update"):
        if not dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if action == "overwrite" and dst.exists():
                shutil.copy2(dst, str(dst) + ".bak")
            shutil.copy2(src, dst)
        recorded[rel] = new_hash
    elif action == "conflict" and not dry:
        shutil.copy2(src, str(dst) + ".new")
    log.note(action, rel)


def is_foreign_dir(dst_dir, prefix, manifest):
    """A skill folder that exists but was not installed by us belongs to the user: leave it whole."""
    return dst_dir.is_dir() and not any(k.startswith(prefix) for k in manifest["files"])


def install_tree(src_root, target, rel_dir, manifest, a, update, log, mirror_to=None):
    """Copy src_root/<rel_dir> to target/<mirror_to or rel_dir>. Skills are handled per skill folder."""
    out = mirror_to or rel_dir
    base = src_root / rel_dir
    per_skill = rel_dir == "skills"
    foreign = set()
    for f in iter_files(base):
        sub = f.relative_to(base).as_posix()
        top = sub.split("/")[0]
        rel = f"{out}/{sub}"
        if per_skill:
            if top in foreign:
                continue
            if is_foreign_dir(target / out / top, f"{out}/{top}/", manifest):
                foreign.add(top)
                log.note("skip-dir", f"{out}/{top}/ (da co skill cung ten cua ban, khong dung vao)")
                continue
        place(f, target / rel, rel, manifest, a.force, update, a.dry_run, log)


def setup_instructions(src_root, target, agent, a, log):
    """Never edits an existing AGENTS.md/CLAUDE.md. The framework rules live in
    .ai-pipeline/AGENTS.md; they are linked only if the files don't exist yet or the user passes --link."""
    place(src_root / "AGENTS.md", target / ".ai-pipeline" / "AGENTS.md", ".ai-pipeline/AGENTS.md",
          a.manifest, a.force, False, a.dry_run, log)
    agents_md, claude_md = target / "AGENTS.md", target / "CLAUDE.md"
    pointer = "@.ai-pipeline/AGENTS.md\n"
    if not agents_md.exists():
        write_text(agents_md, pointer, a.dry_run)
        log.note("add", "AGENTS.md (chi tro toi .ai-pipeline/AGENTS.md)")
    elif a.link:
        if merge_block(agents_md, pointer, a.dry_run):
            log.note("linked", "AGENTS.md (them khoi marker, go bang `ai-pipeline uninstall`)")
    else:
        log.warn.append("AGENTS.md da co -> KHONG sua. De agent doc luat ai-pipeline, them dong "
                        "`@.ai-pipeline/AGENTS.md` (hoac chay lai voi --link). Cac skill van chay doc lap.")
    if agent in ("claude", "both"):
        if not claude_md.exists():
            write_text(claude_md, "@AGENTS.md\n", a.dry_run)
            log.note("add", "CLAUDE.md (@AGENTS.md)")
        elif "AGENTS.md" not in claude_md.read_text(encoding="utf-8"):
            log.warn.append("CLAUDE.md da co -> KHONG sua (Claude Code van thay skills trong .claude/skills).")


def cmd_init(a, update=False):
    target = Path(a.path).resolve()
    if not target.is_dir():
        if update:
            sys.exit(f"ai-pipeline: {target} khong ton tai")
        if not a.dry_run:
            target.mkdir(parents=True)
    src = payload_dir()
    manifest = load_manifest(target)
    if update and not manifest["version"]:
        sys.exit("ai-pipeline: chua init o day (khong co .ai-pipeline.json). Chay: ai-pipeline init")
    a.manifest = manifest
    log = Log(a.verbose)
    print(f"{'Update' if update else 'Init'} ai-pipeline {__version__} -> {target}{' (dry-run)' if a.dry_run else ''}")
    for d in COPY_DIRS:
        install_tree(src, target, d, manifest, a, update, log)
    if a.agent in ("claude", "both"):
        install_tree(src, target, "skills", manifest, a, update, log, mirror_to=".claude/skills")
    if a.agent in ("codex", "both"):
        install_tree(src, target, "skills", manifest, a, update, log, mirror_to=".agents/skills")
    setup_instructions(src, target, a.agent, a, log)
    if a.gitignore:
        setup_gitignore(target, a.dry_run, log)
    else:
        log.warn.append("De khong commit du lieu chay, them vao .gitignore: " + " ".join(GITIGNORE) + "  (hoac --gitignore)")
    if not a.dry_run:
        manifest.update(version=__version__, agent=a.agent)
        (target / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    if not (target / ".git").exists():
        log.warn.append("Chua la git repo: worker song song can git. Chay `git init` + commit dau tien.")
    print("Ket qua: " + ", ".join(f"{k}={v}" for k, v in sorted(log.count.items())))
    if log.count.get("conflict"):
        print("  File ban da sua duoc giu nguyen; ban moi o <file>.new de ban tu merge.")
    if log.count.get("skip") or log.count.get("skip-dir"):
        print("  File/skill da ton tai KHONG bi ghi de (--force chi ghi de file, co .bak). Chay `ai-pipeline doctor`.")
    for w in log.warn:
        print("  ! " + w)
    if not update:
        print("\nTiep theo:\n  1) ai-pipeline doctor\n  2) viet spec (templates/spec.template.md)\n"
              "  3) mo thu muc trong Claude Code / Codex va noi: Chay ai-pipeline voi spec <file>")
    return 0


def cmd_uninstall(a):
    """Remove only files we installed and the user has not edited; strip our marker blocks."""
    target = Path(a.path).resolve()
    manifest = load_manifest(target)
    if not manifest["version"]:
        sys.exit("ai-pipeline: khong co .ai-pipeline.json o day")
    removed = kept = 0
    for rel, h in sorted(manifest["files"].items()):
        p = target / rel
        if not p.is_file():
            continue
        if sha(p) == h:
            if not a.dry_run:
                p.unlink()
            removed += 1
        else:
            kept += 1
            print(f"  giu nguyen (ban da sua): {rel}")
    for name in ("AGENTS.md", "CLAUDE.md", ".gitignore"):
        p = target / name
        if p.is_file() and BEGIN in p.read_text(encoding="utf-8"):
            t = re.sub(r"\n?" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "",
                       p.read_text(encoding="utf-8"), flags=re.S)
            if not a.dry_run:
                p.write_text(t, encoding="utf-8", newline="\n")
    if not a.dry_run:
        for dirpath, _, _ in sorted(os.walk(target), key=lambda x: -len(x[0])):
            dp = Path(dirpath)
            if ".git" in dp.parts or dp == target:
                continue
            if not any(dp.iterdir()):
                dp.rmdir()
        (target / MANIFEST).unlink()
    print(f"Uninstall: go {removed} file, giu {kept} file ban da sua (runs/ va file cua ban khong bi dong vao).")
    return 0


def which(name):
    return shutil.which(name)


def run_ver(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (r.stdout or r.stderr).strip().splitlines()[0][:60] if (r.stdout or r.stderr).strip() else "ok"
    except Exception:
        return "ok"


def cmd_doctor(a):
    target = Path(a.path).resolve()
    rows = []

    def row(ok, name, detail, hint=""):
        rows.append((ok, name, detail, hint))

    pv = sys.version_info
    row(pv >= (3, 9), "python", f"{pv.major}.{pv.minor}.{pv.micro}", "can Python >= 3.9")
    row(bool(which("git")), "git", run_ver(["git", "--version"]) if which("git") else "-", "bat buoc (worktree song song)")
    isrepo = (target / ".git").exists()
    row(isrepo, "git repo", "co" if isrepo else "chua", "git init && git add -A && git commit -m init")
    orca = os.environ.get("ORCA_CLI_COMMAND") or ("orca-ide" if sys.platform.startswith("linux") else "orca")
    row(bool(which(orca)), f"orca ({orca})", "co" if which(orca) else "-", "cai Orca de chay worker song song")
    for n, why in [("claude", "Claude Code"), ("codex", "Codex")]:
        row(bool(which(n)), n, "co" if which(n) else "-", f"{why}: chi can mot trong hai lam coordinator")
    row(bool(which("docker")), "docker", "co" if which("docker") else "-", "tuy chon (sandbox local/server)")
    m = target / MANIFEST
    row(m.is_file(), "ai-pipeline init", json.loads(m.read_text(encoding="utf-8")).get("version", "?") if m.is_file() else "chua", "ai-pipeline init")
    if m.is_file():
        miss = [d for d in COPY_DIRS if not (target / d).is_dir()]
        row(not miss, "framework dirs", "du" if not miss else "thieu " + ",".join(miss), "ai-pipeline update --force")
        sc = subprocess.run([sys.executable, str(target / "scripts" / "sync_skills.py"), "--check"], capture_output=True, text=True)
        row(sc.returncode == 0, "skills mirrors", "dong bo" if sc.returncode == 0 else "lech", "ai-pipeline sync-skills")
    bad = 0
    for ok, name, detail, hint in rows:
        required = name in ("python", "git", "ai-pipeline init", "framework dirs")
        mark = "OK  " if ok else ("FAIL" if required else "WARN")
        bad += (not ok) and required
        print(f"[{mark}] {name:22} {detail}" + ("" if ok else f"   -> {hint}"))
    return 1 if bad else 0


def cmd_script(a):
    target = Path(a.path).resolve()
    script = ALIASES.get(a.name, a.name)
    if not script.endswith(".py"):
        script += ".py"
    p = target / "scripts" / script
    if not p.is_file():
        sys.exit(f"ai-pipeline: khong co scripts/{script} o {target} (da init chua?)")
    return subprocess.call([sys.executable, str(p), *a.args], cwd=str(target))


def cmd_new_run(a):
    target = Path(a.path).resolve()
    spec = Path(a.spec).resolve()
    if not spec.is_file():
        sys.exit(f"ai-pipeline: khong thay spec {spec}")
    slug = re.sub(r"[^a-z0-9]+", "-", a.name.lower()).strip("-") or "run"
    run = target / "runs" / slug
    if run.exists():
        sys.exit(f"ai-pipeline: {run} da ton tai")
    (run / "artifacts").mkdir(parents=True)
    shutil.copy2(spec, run / "spec.md")
    (run / "decisions.md").write_text("# Decisions\n", encoding="utf-8")
    (run / "done.json").write_text("[]\n", encoding="utf-8")
    print(f"Tao {run}\nTiep: ai-pipeline validate {run / 'spec.md'}")
    return 0


def cmd_version(a):
    print(f"ai-pipeline {__version__}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="ai-pipeline", description=__doc__.strip().splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, init=False):
        sp.add_argument("path", nargs="?", default=".", help="thu muc du an (mac dinh: .)")
        if init:
            sp.add_argument("--agent", choices=["claude", "codex", "both"], default="both")
            sp.add_argument("--force", action="store_true", help="ghi de file da ton tai")
            sp.add_argument("--dry-run", action="store_true")
            sp.add_argument("--link", action="store_true", help="them dong tro vao AGENTS.md co san (mac dinh: khong sua)")
            sp.add_argument("--gitignore", action="store_true", help="them muc vao .gitignore (mac dinh: chi goi y)")
            sp.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("init", help="cai workflow vao du an"); common(s, True); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("update", help="nang cap file framework (giu file ban da sua)"); common(s, True)
    s.set_defaults(fn=lambda a: cmd_init(a, update=True))
    s = sub.add_parser("uninstall", help="go cac file da cai (giu file ban sua)"); common(s)
    s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_uninstall)
    s = sub.add_parser("doctor", help="kiem tra moi truong va cai dat"); common(s); s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("new-run", help="tao runs/<ten>/ tu file spec"); s.add_argument("name"); s.add_argument("spec")
    s.add_argument("--path", default="."); s.set_defaults(fn=cmd_new_run)
    s = sub.add_parser("run", help="chay mot script cua workflow", aliases=["x"])
    s.add_argument("name", help="alias (" + ", ".join(sorted(ALIASES)) + ") hoac ten script")
    s.add_argument("args", nargs=argparse.REMAINDER); s.add_argument("--path", default="."); s.set_defaults(fn=cmd_script)
    s = sub.add_parser("version"); s.set_defaults(fn=cmd_version)
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if argv and argv[0] in ALIASES:  # `ai-pipeline status runs/x` == `ai-pipeline run status runs/x`
        argv = ["run", *argv]
    a = build_parser().parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
