"""Quan ly hook cuong che pipeline_guard trong .claude/settings.json.

Nguyen tac: MERGE, khong pha cau hinh nguoi dung.
- Giu nguyen moi hook/nhom/matcher khac cua nguoi dung (install LUON tao
  nhom RIENG matcher du Read|Edit|MultiEdit|Write|NotebookEdit|Bash|Grep|Glob;
  khong bao gio chen vao hay sua matcher cua nhom nguoi dung).
- Nhan dien muc cua ai-pipeline bang noi dung command (chua
  "pipeline_guard.py"), khong them khoa la vao schema settings.
- Uninstall chi go handler/nhom co marker cua ai-pipeline.
- Idempotent: cai 2 lan khong doi file lan 2.
- JSON hong -> bao loi, KHONG ghi de.

Stdlib only. Da nen tang (pathlib, encoding utf-8, newline LF).
"""
import json
from pathlib import Path

MARKER = "pipeline_guard.py"
SETTINGS_REL = Path(".claude") / "settings.json"
# Nhom matcher RIENG cua ai-pipeline (RV5: khong bao gio dung chung hay sua
# matcher cua nhom nguoi dung, vi Bash khong kich hoat nhom matcher Read).
# Danh sach tool theo giao thuc hook Claude Code hien co trong task.
MATCHER = "Read|Edit|MultiEdit|Write|NotebookEdit|Bash|Grep|Glob"


class HooksError(RuntimeError):
    """Loi hooks (JSON hong, duong dan sai)."""


def handler_entry():
    """1 hook handler exec-form, chay da nen tang, khong shell quoting."""
    return {
        "type": "command",
        "command": "python",
        "args": ["${CLAUDE_PROJECT_DIR}/scripts/pipeline_guard.py"],
    }


def settings_path(project):
    return Path(project).resolve() / SETTINGS_REL


def _handler_is_ours(h):
    if not isinstance(h, dict):
        return False
    blob = json.dumps(h, ensure_ascii=False)
    return MARKER in blob


def load_settings(path):
    """Doc settings. Thieu file -> {}. Hong -> HooksError (khong ghi de)."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise HooksError(f"{p} khong phai JSON hop le ({e}); khong sua gi ca. "
                         f"Sua/xoa tay roi chay lai.") from e
    if not isinstance(data, dict):
        raise HooksError(f"{p} phai la object JSON; khong sua gi ca.")
    return data


def _write(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8", newline="\n")


def find_ours(data):
    """Dem handler cua ai-pipeline trong settings."""
    n = 0
    for group in (data.get("hooks") or {}).get("PreToolUse", []):
        if not isinstance(group, dict):
            continue
        for h in group.get("hooks", []):
            if _handler_is_ours(h):
                n += 1
    return n


def _group_is_ours(group):
    """True khi nhom PreToolUse nay la nhom RIENG cua ai-pipeline
    (chua handler co marker)."""
    if not isinstance(group, dict):
        return False
    for h in group.get("hooks", []):
        if _handler_is_ours(h):
            return True
    return False


def install(project=".", dry_run=False):
    """Merge hook vao settings hien co. Tra (changed: bool, messages: list).

    NGUYEN TAC (RV5): luon tao NHOM RIENG matcher du cua ai-pipeline;
    khong bao gio chen vao, sua hay de matcher cua nhom nguoi dung."""
    sp = settings_path(project)
    data = load_settings(sp)
    if find_ours(data):
        return False, [f"garda hook da co trong {sp} (idempotent, khong doi gi)"]
    ours = handler_entry()
    hooks = data.setdefault("hooks", {})
    groups = hooks.setdefault("PreToolUse", [])
    if not isinstance(groups, list):
        raise HooksError(f"{sp}: hooks.PreToolUse phai la list; khong sua gi ca.")
    groups.append({"matcher": MATCHER, "hooks": [dict(ours)]})
    msgs = [f"them nhom PreToolUse RIENG (matcher {MATCHER}) vao {sp}",
            "giua nguyen moi hook/nhom/matcher khac cua ban"]
    if not dry_run:
        _write(sp, data)
    else:
        msgs.append("(dry-run: chua ghi file)")
    return True, msgs


def uninstall(project=".", dry_run=False):
    """Go sach muc cua ai-pipeline, giu lai hook nguoi dung. Tra (changed, msgs).

    Chi go handler co marker trong nhom co marker; matcher/handler cua nguoi
    dung giu nguyen; nhom rong sau khi go thi bo luon."""
    sp = settings_path(project)
    data = load_settings(sp)
    before = find_ours(data)
    if not before:
        return False, [f"khong thay hook ai-pipeline trong {sp} (khong doi gi)"]
    groups = (data.get("hooks") or {}).get("PreToolUse", [])
    kept_groups = []
    for g in groups:
        if not isinstance(g, dict):
            kept_groups.append(g)
            continue
        if not _group_is_ours(g):
            kept_groups.append(g)  # nhom nguoi dung: khong dung toi
            continue
        kept = [h for h in g.get("hooks", []) if not _handler_is_ours(h)]
        if kept:
            g["hooks"] = kept
            kept_groups.append(g)
        # nhom rong sau khi go -> bo luon
    if kept_groups:
        data["hooks"]["PreToolUse"] = kept_groups
    else:
        data["hooks"].pop("PreToolUse", None)
        if not data["hooks"]:
            data.pop("hooks", None)
    msgs = [f"go {before} hook ai-pipeline khoi {sp}",
            "hook cua ban duoc giu nguyen"]
    if not dry_run:
        _write(sp, data)
    else:
        msgs.append("(dry-run: chua ghi file)")
    return True, msgs


def status(project="."):
    """Tra dict trang thai de CLI in (tieng Viet o CLI)."""
    sp = settings_path(project)
    info = {"settings": str(sp), "installed": False, "ours": 0,
            "exists": sp.is_file(), "guard_script": False, "policy": None}
    root = Path(project).resolve()
    info["guard_script"] = (root / "scripts" / "pipeline_guard.py").is_file()
    for cand in (root / ".ai-pipeline" / "guard_policy.json",
                 root / "guard_policy.json"):
        if cand.is_file():
            info["policy"] = str(cand)
            break
    if sp.is_file():
        try:
            info["ours"] = find_ours(load_settings(sp))
            info["installed"] = info["ours"] > 0
        except HooksError as e:
            info["error"] = str(e)
    return info


def run(argv):
    """CLI: ai-pipeline hooks install|status|uninstall [--path P] [--dry-run]."""
    import argparse
    p = argparse.ArgumentParser(prog="ai-pipeline hooks")
    p.add_argument("action", choices=["install", "status", "uninstall"])
    p.add_argument("--path", default=".")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.action == "install":
            changed, msgs = install(a.path, a.dry_run)
        elif a.action == "uninstall":
            changed, msgs = uninstall(a.path, a.dry_run)
        else:
            info = status(a.path)
            print(f"settings: {info['settings']}")
            print(f"  file: {'co' if info['exists'] else 'chua co'}")
            if info.get("error"):
                print(f"  LOI: {info['error']}")
                return 1
            print(f"  hook ai-pipeline: {'DA CAI' if info['installed'] else 'chua cai'}")
            print(f"  scripts/pipeline_guard.py: {'co' if info['guard_script'] else 'thieu'}")
            print(f"  guard_policy: {info['policy'] or 'mac dinh (chua co file)'}")
            return 0
        for m in msgs:
            print(("~ " if a.dry_run else ("+ " if changed else "= ")) + m)
        return 0
    except HooksError as e:
        print(f"ai-pipeline hooks: {e}")
        return 1
