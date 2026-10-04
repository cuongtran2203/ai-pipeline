#!/usr/bin/env python3
"""Per-problem lab notebook: experiment journal + distilled insights, exportable to NotebookLM.

  notebook.py init   <run_dir> [--title T]
  notebook.py log    <run_dir> --type experiment|decision|insight|research|error|gate --title T --body B
                     [--tags a,b] [--metrics '{"val_acc":0.9866}'] [--refs path1,path2] [--author who]
  notebook.py export <run_dir>            -> notebook/export/notebook-<date>.md  (one markdown source to upload)
  notebook.py show   <run_dir> [--type X] [--last N]

Layout: <run_dir>/notebook/{journal.jsonl (source of truth, append-only), journal.md, insights.md,
        notebooklm.json, export/}. Stdlib only; safe to call from several workers (append + lock-free).
`insights.md` is the curated "đúc kết" section: it is rebuilt from entries of type insight/decision.
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
TYPES = ("experiment", "decision", "insight", "research", "error", "gate")
ICON = {"experiment": "🧪", "decision": "⚖️", "insight": "💡", "research": "📚", "error": "🐞", "gate": "🚦"}


def nb_dir(run_dir):
    return os.path.join(os.path.abspath(run_dir), "notebook")


def read_entries(run_dir):
    p = os.path.join(nb_dir(run_dir), "journal.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


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
    es = read_entries(run_dir)
    meta = {}
    try:
        meta = json.load(open(os.path.join(d, "notebooklm.json"), encoding="utf-8"))
    except (OSError, ValueError):
        pass
    title = meta.get("title", os.path.basename(os.path.abspath(run_dir)))
    with open(os.path.join(d, "journal.md"), "w", encoding="utf-8") as f:
        f.write(f"# Sổ thí nghiệm: {title}\n\nTheo thứ tự thời gian. Nguồn gốc: `journal.jsonl`.\n\n" + "\n".join(fmt_entry(e) for e in es))
    keep = [e for e in es if e["type"] in ("insight", "decision")]
    with open(os.path.join(d, "insights.md"), "w", encoding="utf-8") as f:
        f.write(f"# Đúc kết & quyết định: {title}\n\n" + ("\n".join(fmt_entry(e) for e in keep) if keep else "_Chưa có._\n"))


def cmd_init(a):
    d = nb_dir(a.run_dir)
    os.makedirs(os.path.join(d, "export"), exist_ok=True)
    p = os.path.join(d, "notebooklm.json")
    if not os.path.exists(p):
        json.dump({"title": a.title or os.path.basename(os.path.abspath(a.run_dir)), "notebook_url": "",
                   "sources_uploaded": [], "last_export": "", "note": "NotebookLM notebook/sources are created by the human; see skills/ai-pipeline-notebook"},
                  open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    if not os.path.exists(os.path.join(d, "journal.jsonl")):
        open(os.path.join(d, "journal.jsonl"), "w", encoding="utf-8").close()
    rebuild(a.run_dir)
    print("notebook ready:", d)


import re


def slug(s, n=48):
    s = re.sub(r"[^\w\- ]+", "", s, flags=re.U).strip().replace(" ", "-")
    return (s[:n] or "entry").strip("-").lower()


def sync_to_kg(run_dir, e, kg_edges_arg=None):
    try:
        kd = os.path.join(os.path.abspath(run_dir), "knowledge")
        os.makedirs(kd, exist_ok=True)
        ep = os.path.join(kd, "entities.jsonl")
        edp = os.path.join(kd, "edges.jsonl")

        type_map = {
            "decision": "Decision",
            "gate": "Decision",
            "experiment": "Experiment",
            "research": "Experiment",
            "insight": "Experiment",
            "error": "Incident",
        }
        kg_type = type_map.get(e["type"], "Experiment")
        prefix_map = {
            "Decision": "decision",
            "Experiment": "exp",
            "Incident": "incident",
        }
        prefix = prefix_map.get(kg_type, "node")
        eid = f"{prefix}:{slug(e['title'])}"

        # 1. Append entity
        ent_record = {
            "id": eid,
            "type": kg_type,
            "title": e["title"],
            "body": e["body"],
            "properties": {
                "author": e.get("author"),
                "tags": e.get("tags", []),
                "metrics": e.get("metrics", {}),
                "refs": e.get("refs", []),
            },
            "created_at": e["ts"],
        }
        with open(ep, "a", encoding="utf-8") as f:
            f.write(json.dumps(ent_record, ensure_ascii=False) + "\n")

        # 2. Author person entity
        author = e.get("author") or "agent"
        author_id = f"person:{slug(author)}"
        author_record = {
            "id": author_id,
            "type": "Person",
            "title": author,
            "body": "",
            "properties": {"alias": author},
            "created_at": e["ts"],
        }
        with open(ep, "a", encoding="utf-8") as f:
            f.write(json.dumps(author_record, ensure_ascii=False) + "\n")

        # 3. Automatic edges
        edges_to_write = []
        if kg_type == "Decision":
            edges_to_write.append({
                "source": eid,
                "target": author_id,
                "type": "decided_by",
                "valid_from": e["ts"],
                "valid_to": None,
                "recorded_at": e["ts"],
                "source_ref": f"notebook:{e['ts']}",
                "confidence": 1.0,
            })

        # Parse refs for artifacts / datasets / models
        for r in e.get("refs", []):
            clean_r = r.strip()
            if not clean_r:
                continue
            r_slug = slug(os.path.basename(clean_r))
            art_id = f"artifact:{r_slug}"
            with open(ep, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "id": art_id,
                    "type": "Artifact",
                    "title": os.path.basename(clean_r),
                    "body": f"Referenced at {clean_r}",
                    "properties": {"path": clean_r},
                    "created_at": e["ts"],
                }, ensure_ascii=False) + "\n")

            if kg_type == "Experiment":
                edges_to_write.append({
                    "source": eid,
                    "target": art_id,
                    "type": "evaluated_on",
                    "valid_from": e["ts"],
                    "valid_to": None,
                    "recorded_at": e["ts"],
                    "source_ref": clean_r,
                    "confidence": 1.0,
                })
            elif kg_type == "Decision":
                edges_to_write.append({
                    "source": eid,
                    "target": art_id,
                    "type": "evidenced_by",
                    "valid_from": e["ts"],
                    "valid_to": None,
                    "recorded_at": e["ts"],
                    "source_ref": clean_r,
                    "confidence": 1.0,
                })

        # 4. Explicit --kg-edges
        if kg_edges_arg:
            for item in kg_edges_arg.split(","):
                item = item.strip()
                if not item or ":" not in item:
                    continue
                etype, tgt = item.split(":", 1)
                etype = etype.strip()
                tgt = tgt.strip()
                edges_to_write.append({
                    "source": eid,
                    "target": tgt,
                    "type": etype,
                    "valid_from": e["ts"],
                    "valid_to": None,
                    "recorded_at": e["ts"],
                    "source_ref": f"notebook:{e['ts']}",
                    "confidence": 1.0,
                })

        # Deduplicate edges before writing
        seen_edges = set()
        if os.path.exists(edp):
            with open(edp, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            re = json.loads(line)
                            seen_edges.add((re.get("source"), re.get("target"), re.get("type")))
                        except ValueError:
                            pass

        with open(edp, "a", encoding="utf-8") as f:
            for ed in edges_to_write:
                key = (ed["source"], ed["target"], ed["type"])
                if key not in seen_edges:
                    seen_edges.add(key)
                    f.write(json.dumps(ed, ensure_ascii=False) + "\n")
    except Exception as ex:
        print(f"Warning: KG sync skipped ({ex})", file=sys.stderr)


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
    with open(os.path.join(nb_dir(a.run_dir), "journal.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    rebuild(a.run_dir)
    if not getattr(a, "no_kg", False):
        sync_to_kg(a.run_dir, e, getattr(a, "kg_edges", None))
    print(f"logged [{a.type}] {a.title}")


def cmd_export(a):
    es = read_entries(a.run_dir)
    d = nb_dir(a.run_dir)
    rebuild(a.run_dir)
    meta = json.load(open(os.path.join(d, "notebooklm.json"), encoding="utf-8"))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    out = os.path.join(d, "export", f"notebook-{stamp}.md")
    counts = {t: sum(1 for e in es if e["type"] == t) for t in TYPES}
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# {meta['title']} — sổ thí nghiệm (xuất {stamp})\n\n"
                f"Tổng: {len(es)} mục ({', '.join(f'{k}={v}' for k, v in counts.items() if v)}).\n\n"
                "Không chứa dữ liệu khách hàng/bí mật; chỉ kết luận, số đo và đường dẫn.\n\n"
                + open(os.path.join(d, "insights.md"), encoding="utf-8").read().replace("# ", "## ", 1) + "\n"
                + open(os.path.join(d, "journal.md"), encoding="utf-8").read().replace("# ", "## ", 1))
    meta["last_export"] = out
    json.dump(meta, open(os.path.join(d, "notebooklm.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("exported:", out, "\n→ upload/replace this file as a source in the problem's NotebookLM notebook (human step).")


def cmd_show(a):
    es = [e for e in read_entries(a.run_dir) if not a.type or e["type"] == a.type]
    for e in es[-a.last:]:
        print(fmt_entry(e))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("init", "log", "export", "show"):
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
    {"init": cmd_init, "log": cmd_log, "export": cmd_export, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    main()
