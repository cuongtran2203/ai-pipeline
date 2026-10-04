#!/usr/bin/env python3
"""Per-problem lab notebook: experiment journal + distilled insights, exportable to NotebookLM.

  notebook.py init      <run_dir> [--title T]
  notebook.py log       <run_dir> --type experiment|decision|insight|research|error|gate --title T --body B
                        [--tags a,b] [--metrics '{"val_acc":0.9866}'] [--refs path1,path2] [--author who]
  notebook.py export    <run_dir>            -> notebook/export/notebook-<date>.md (one markdown source to upload)
  notebook.py show      <run_dir> [--type X] [--last N]
  notebook.py reconcile <run_dir>            -> retry pending KG syncs recorded in notebook/sync_pending.json

Layout: <run_dir>/notebook/{journal.jsonl (source of truth, append-only), journal.md, insights.md,
        notebooklm.json, export/}. Stdlib only.

Concurrency: the journal ordinal + append, and the markdown rebuild, are protected by inter-process
locks; a new entry gets a UUID in its source key so identical entries never collide and an entry is
never lost. Legacy entries without `id` get a stable key exactly once (journal.jsonl.bak-<ts> backup)
instead of being recomputed by position on every read. KG writes go through the validated kg.py API;
a sync failure is recorded in notebook/sync_pending.json instead of being swallowed silently.
`insights.md` is the curated "đúc kết" section rebuilt from entries of type insight/decision.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kg  # noqa: E402  (validated single write API for the knowledge graph)
import statefile  # noqa: E402  (transactional JSON writes)

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
TYPES = ("experiment", "decision", "insight", "research", "error", "gate")
ICON = {"experiment": "🧪", "decision": "⚖️", "insight": "💡", "research": "📚", "error": "🐞", "gate": "🚦"}


def nb_dir(run_dir):
    return os.path.join(os.path.abspath(run_dir), "notebook")


def journal_path(run_dir):
    return os.path.join(nb_dir(run_dir), "journal.jsonl")


def _journal_guard(run_dir):
    """Guard for the journal critical section (ordinal+append). Distinct from journal.jsonl.lock."""
    return os.path.join(nb_dir(run_dir), ".journal")


def _render_guard(run_dir):
    return os.path.join(nb_dir(run_dir), ".render")


def _sync_pending_path(run_dir):
    return os.path.join(nb_dir(run_dir), "sync_pending.json")


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _atomic_write_text(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        statefile._replace_atomic(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_jsonl(path, rows):
    _atomic_write_text(path, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in rows))


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _read_raw_entries(run_dir):
    # strict=True: dòng hỏng ở GIỮA journal (do ghi dở/chen ngang) phải báo lỗi, không bỏ qua im lặng.
    return statefile.read_jsonl(journal_path(run_dir), strict=True)


def ensure_journal_ids(run_dir):
    """Assign a stable source key to legacy entries exactly once, with a backup.

    Legacy entries (no `id`) are keyed by position at migration time and the ids are persisted,
    so two identical entries become two distinct nodes and later re-reads never recompute them.
    """
    jp = journal_path(run_dir)
    if not os.path.exists(jp):
        return []
    entries = _read_raw_entries(run_dir)
    if not any(not e.get("id") for e in entries):
        return entries  # fast path: nothing to migrate, no lock contention with the append path
    with statefile.file_lock(_journal_guard(run_dir)):
        entries = _read_raw_entries(run_dir)
        missing = [i for i, e in enumerate(entries) if not e.get("id")]
        if not missing:
            return entries
        backup = jp + ".bak-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(jp, backup)
        for i in missing:
            entries[i]["id"] = kg.source_entry_id(run_dir, i, entries[i])
        _atomic_write_jsonl(jp, entries)
        print(f"notebook: cấp id cho {len(missing)} mục cũ; backup {backup}", file=sys.stderr)
        return entries


def read_entries(run_dir, migrate=False):
    if migrate:
        return ensure_journal_ids(run_dir)
    return _read_raw_entries(run_dir)


def new_entry_id(run_dir, index, entry):
    """Source key for a fresh entry: run + ordinal + content hash + UUID.

    The UUID makes two identical entries in the same minute distinct, so they never merge into
    one KG node and no counter/timestamp collision can drop an entry.
    """
    return f"{kg.source_entry_id(run_dir, index, entry)}-{uuid.uuid4().hex[:8]}"


def fmt_entry(e):
    lines = [f"### {ICON.get(e['type'], '•')} {e['title']}",
             f"*{e['ts']} · {e['type']} · {e.get('author', '-')}" + (f" · tags: {', '.join(e['tags'])}" if e.get("tags") else "") + "*", ""]
    lines.append(e["body"].strip())
    if e.get("metrics"):
        lines += ["", "Metrics: " + ", ".join(f"`{k}`={v}" for k, v in e["metrics"].items())]
    if e.get("refs"):
        lines += ["", "Refs: " + ", ".join(f"`{r}`" for r in e["refs"])]
    return "\n".join(lines) + "\n"


def rebuild(run_dir):
    d = nb_dir(run_dir)
    # Chụp snapshot journal BÊN TRONG render lock: một rebuild cũ không thể ghi đè rebuild mới,
    # vì rebuild sau chỉ đọc journal sau khi rebuild trước đã nhả khóa. journal.md luôn đủ mục.
    with statefile.file_lock(_render_guard(run_dir)):
        es = ensure_journal_ids(run_dir)
        meta = {}
        try:
            with open(os.path.join(d, "notebooklm.json"), encoding="utf-8-sig") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            pass
        title = meta.get("title", os.path.basename(os.path.abspath(run_dir)))
        journal_md = (f"# Sổ thí nghiệm: {title}\n\nTheo thứ tự thời gian. Nguồn gốc: `journal.jsonl`.\n\n"
                      + "\n".join(fmt_entry(e) for e in es))
        keep = [e for e in es if e["type"] in ("insight", "decision")]
        insights_md = (f"# Đúc kết & quyết định: {title}\n\n"
                       + ("\n".join(fmt_entry(e) for e in keep) if keep else "_Chưa có._\n"))
        _atomic_write_text(os.path.join(d, "journal.md"), journal_md)
        _atomic_write_text(os.path.join(d, "insights.md"), insights_md)


def cmd_init(a):
    d = nb_dir(a.run_dir)
    os.makedirs(os.path.join(d, "export"), exist_ok=True)
    meta = {"title": a.title or os.path.basename(os.path.abspath(a.run_dir)), "notebook_url": "",
            "sources_uploaded": [], "last_export": "",
            "note": "NotebookLM notebook/sources are created by the human; see skills/ai-pipeline-notebook"}
    statefile.update_json(os.path.join(d, "notebooklm.json"),
                          lambda data: data if isinstance(data, dict) and data else meta, default={})
    jp = journal_path(a.run_dir)
    if not os.path.exists(jp):
        try:
            fd = os.open(jp, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o644)
        except FileExistsError:
            pass
        else:
            os.close(fd)
    rebuild(a.run_dir)
    print("notebook ready:", d)


def slug(s, n=48):
    """Backward-compatible wrapper around the shared kg.slug."""
    return kg.slug(s, n)


def _sync_entry(run_dir, e, kg_edges_arg=None):
    """Write notebook knowledge through kg.py's validated API. Raises on any KG error."""
    type_map = {
        "decision": "Decision",
        "gate": "Decision",
        "experiment": "Experiment",
        "research": "Experiment",
        "insight": "Experiment",
        "error": "Incident",
    }
    kg_type = type_map.get(e["type"], "Experiment")
    prefix = {"Decision": "decision", "Experiment": "exp", "Incident": "incident"}.get(kg_type, "node")
    src_id = e.get("id") or kg.source_entry_id(run_dir, 0, e)
    eid = f"{prefix}:{kg.slug(src_id)}"

    kg.upsert_entity(
        run_dir, eid, kg_type, e["title"], body=e["body"],
        properties={
            "author": e.get("author"),
            "tags": e.get("tags", []),
            "metrics": e.get("metrics", {}),
            "refs": e.get("refs", []),
            "source_key": src_id,
        },
        created_at=e["ts"],
    )

    author = e.get("author") or "agent"
    author_id = f"person:{kg.slug(author)}"
    kg.upsert_entity(run_dir, author_id, "Person", author,
                     properties={"alias": author}, created_at=e["ts"])

    if kg_type == "Decision":
        kg.add_edge_checked(run_dir, eid, author_id, "decided_by", valid_from=e["ts"],
                            recorded_at=e["ts"], source_ref=f"notebook:{e['ts']}")

    for r in e.get("refs", []):
        clean_r = kg.normalize_ref_path(r)
        if not clean_r:
            print(f"Warning: bỏ qua ref ngoài gốc dự án: {r}", file=sys.stderr)
            continue
        art_id = kg.upsert_artifact_ref(run_dir, clean_r, created_at=e["ts"])
        edge_type = "evaluated_on" if kg_type == "Experiment" else ("evidenced_by" if kg_type == "Decision" else None)
        if edge_type:
            kg.add_edge_checked(run_dir, eid, art_id, edge_type, valid_from=e["ts"],
                                recorded_at=e["ts"], source_ref=clean_r)

    if kg_edges_arg:
        for item in kg_edges_arg.split(","):
            item = item.strip()
            if not item or ":" not in item:
                continue
            etype, tgt = (x.strip() for x in item.split(":", 1))
            try:
                kg.add_edge_checked(run_dir, eid, tgt, etype, valid_from=e["ts"],
                                    recorded_at=e["ts"], source_ref=f"notebook:{e['ts']}")
            except kg.KgError as ex:
                print(f"Warning: bỏ qua cạnh KG không hợp lệ ({etype} -> {tgt}): {ex}", file=sys.stderr)


def _record_sync_pending(run_dir, entry_id, error):
    def fn(data):
        data = data if isinstance(data, dict) else {}
        data[str(entry_id)] = {"error": str(error), "ts": _now()}
        return data
    statefile.update_json(_sync_pending_path(run_dir), fn, default={})


def sync_to_kg(run_dir, e, kg_edges_arg=None):
    """Sync one entry to the KG; return True/False and record a pending sync on failure.

    Never swallows the exception silently: the failure is surfaced on stderr AND persisted so
    `notebook.py reconcile` can retry it without re-running the experiment."""
    try:
        _sync_entry(run_dir, e, kg_edges_arg)
        return True
    except Exception as ex:  # noqa: BLE001  (record pending, do not lose the journal entry)
        print(f"Warning: KG sync skipped ({ex})", file=sys.stderr)
        _record_sync_pending(run_dir, e.get("id"), ex)
        return False


def cmd_log(a):
    if not os.path.isdir(nb_dir(a.run_dir)):
        cmd_init(argparse.Namespace(run_dir=a.run_dir, title=None))
    try:
        metrics = json.loads(a.metrics) if a.metrics else {}
    except ValueError:
        sys.exit("--metrics must be a JSON object")
    e = {"ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "type": a.type, "title": a.title, "body": a.body,
         "author": a.author or os.environ.get("USER") or "agent", "tags": [t for t in (a.tags or "").split(",") if t],
         "metrics": metrics, "refs": [r for r in (a.refs or "").split(",") if r]}
    # Allocate the ordinal and append under the journal lock: two workers cannot share an ordinal
    # and no entry is lost. The UUID in the id makes identical entries distinct.
    with statefile.file_lock(_journal_guard(a.run_dir)):
        existing = _read_raw_entries(a.run_dir)
        e["id"] = new_entry_id(a.run_dir, len(existing), e)
        statefile.append_jsonl(journal_path(a.run_dir), e)
    rebuild(a.run_dir)
    if not getattr(a, "no_kg", False):
        sync_to_kg(a.run_dir, e, getattr(a, "kg_edges", None))
    print(f"logged [{a.type}] {a.title}")


def cmd_reconcile(a):
    pending = statefile.read_json(_sync_pending_path(a.run_dir), {})
    pending = pending if isinstance(pending, dict) else {}
    if not pending:
        print("no pending KG sync")
        return 0
    entries = {e.get("id"): e for e in ensure_journal_ids(a.run_dir)}
    failed, succeeded = {}, {}
    for eid, rec in pending.items():
        e = entries.get(eid)
        if e is None:
            failed[eid] = {"error": "mục journal không còn tồn tại", "ts": _now()}
            print(f"  still pending: {eid} (entry missing)", file=sys.stderr)
            continue
        try:
            _sync_entry(a.run_dir, e, None)
        except Exception as ex:  # noqa: BLE001
            failed[eid] = {"error": str(ex), "ts": _now()}
            print(f"  still pending: {eid} ({ex})", file=sys.stderr)
        else:
            succeeded[eid] = rec
            print(f"  reconciled: {eid}")

    def fn(data):
        data = data if isinstance(data, dict) else {}
        # Chỉ xoá key đã sync thành công khi giá trị hiện tại VẪN khớp giá trị đã đọc;
        # key do writer khác thêm/đổi trong lúc sync được giữ nguyên (không xoá công việc mới).
        for eid, rec in succeeded.items():
            if data.get(eid) == rec:
                data.pop(eid, None)
        for eid, rec in failed.items():
            data[eid] = rec
        return data

    statefile.update_json(_sync_pending_path(a.run_dir), fn, default={})
    print(f"reconcile: {len(succeeded)} ok, {len(failed)} còn pending")
    return 1 if failed else 0


def cmd_export(a):
    es = read_entries(a.run_dir, migrate=True)
    d = nb_dir(a.run_dir)
    rebuild(a.run_dir)
    meta = {}
    try:
        with open(os.path.join(d, "notebooklm.json"), encoding="utf-8-sig") as f:
            meta = json.load(f)
    except (OSError, ValueError):  # run cũ thiếu metadata vẫn export được
        meta = {"title": os.path.basename(os.path.abspath(a.run_dir))}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    os.makedirs(os.path.join(d, "export"), exist_ok=True)
    out = os.path.join(d, "export", f"notebook-{stamp}.md")
    counts = {t: sum(1 for e in es if e["type"] == t) for t in TYPES}
    text = (f"# {meta.get('title', '')} — sổ thí nghiệm (xuất {stamp})\n\n"
            f"Tổng: {len(es)} mục ({', '.join(f'{k}={v}' for k, v in counts.items() if v)}).\n\n"
            "Không chứa dữ liệu khách hàng/bí mật; chỉ kết luận, số đo và đường dẫn.\n\n"
            + _read_text(os.path.join(d, "insights.md")).replace("# ", "## ", 1) + "\n"
            + _read_text(os.path.join(d, "journal.md")).replace("# ", "## ", 1))
    _atomic_write_text(out, text)
    def set_last(data):
        data = data if isinstance(data, dict) else {}
        data["last_export"] = out
        return data
    statefile.update_json(os.path.join(d, "notebooklm.json"), set_last, default={})
    print("exported:", out, "\n→ upload/replace this file as a source in the problem's NotebookLM notebook (human step).")


def cmd_show(a):
    es = [e for e in ensure_journal_ids(a.run_dir) if not a.type or e["type"] == a.type]
    for e in es[-a.last:]:
        print(fmt_entry(e))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("init", "log", "export", "show", "reconcile"):
        p = sp.add_parser(n)
        p.add_argument("run_dir")
        if n == "init":
            p.add_argument("--title")
        if n == "log":
            p.add_argument("--type", required=True, choices=TYPES)
            p.add_argument("--title", required=True)
            p.add_argument("--body", required=True)
            p.add_argument("--tags")
            p.add_argument("--metrics")
            p.add_argument("--refs")
            p.add_argument("--author")
            p.add_argument("--kg-edges", help="Comma-separated typed edges (e.g. supersedes:decision:G1,evidenced_by:exp:B0)")
            p.add_argument("--no-kg", action="store_true", help="Disable automatic KG entity/edge generation")
        if n == "show":
            p.add_argument("--type", choices=TYPES)
            p.add_argument("--last", type=int, default=20)
    a = ap.parse_args()
    cmds = {"init": cmd_init, "log": cmd_log, "export": cmd_export, "show": cmd_show, "reconcile": cmd_reconcile}
    if a.cmd == "reconcile":
        sys.exit(cmd_reconcile(a))
    cmds[a.cmd](a)


if __name__ == "__main__":
    main()
