#!/usr/bin/env python3
"""Build a local Obsidian vault that graphs the project's DOCUMENTS (and optionally its code graph).

  obsidian_vault.py [--run runs/<id> ...] [--out vault] [--graphify <path to graphify exe>]

Vault layout (open `<out>` as a vault in Obsidian; Graph view then shows the links):
  INDEX.md                      hub linking every run's map of content
  docs/<project paths>.md       copies of AGENTS.md, README, skills/*/SKILL.md, roles/*.md, templates, and per run
                                spec/decisions/feasibility, artifacts/**.md, modules/**/report.md, reports/**.md
  docs/runs/<id>/notebook/entries/NNN-slug.md   one note per notebook entry (tags = type, `Refs` become [[wikilinks]])
  docs/runs/<id>/INDEX.md       map of content: decisions, insights, experiments, errors, reports, artifacts
  code/                         Graphify's `export obsidian` of graphify-out/graph.json (only with --graphify)

Stdlib only, nothing leaves the machine, derived output (keep `vault/` out of git). Excludes data, images,
checkpoints, notebook/export copies, graphify-out and virtualenvs. Safe to rerun: only a folder carrying the
`.vault-generated` marker is ever deleted.
"""
import argparse
import collections
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kg  # noqa: E402  (shared stable source-key helper)

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP_DOCS = ["AGENTS.md", "README.md"]
TOP_GLOBS = ["skills/*/SKILL.md", "roles/*.md", "templates/*.md"]
SKIP_PARTS = ("/data/", "/export/", "/__pycache__/", "/img/", "/models/", "/results/", "/blind/")
KEY_ENV = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "MOONSHOT_API_KEY", "DEEPSEEK_API_KEY"]


def slug(s, n=48):
    s = re.sub(r"[^\w\- ]+", "", s, flags=re.U).strip().replace(" ", "-")
    return (s[:n] or "entry").strip("-")


def copy_md(src, out_docs):
    rel = os.path.relpath(src, ROOT).replace("\\", "/")
    dst = os.path.join(out_docs, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return rel[:-3]  # wikilink target without .md


def wikilink(ref, known):
    ref = ref.replace("\\", "/")
    if ref.startswith(ROOT.replace("\\", "/")):
        ref = ref[len(ROOT.replace("\\", "/")) + 1:]
    base = ref[:-3] if ref.endswith(".md") else ref
    return f"[[{base}]]" if base in known else f"`{ref}`"


def _write(path, text):
    """Atomic text write (temp + os.replace) so two renderers sharing an output never write half a file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_run(run_dir, out_docs, known):
    run_dir = os.path.abspath(run_dir)
    rid = os.path.basename(run_dir)
    rel_run = f"runs/{rid}"
    copied = []
    for p in glob.glob(os.path.join(run_dir, "**", "*.md"), recursive=True):
        q = p.replace("\\", "/")
        if any(part in q for part in SKIP_PARTS) or "/notebook/" in q:
            continue
        known.add(copy_md(p, out_docs))
        copied.append(os.path.relpath(p, ROOT).replace("\\", "/")[:-3])
    jl = os.path.join(run_dir, "notebook", "journal.jsonl")
    entries = []
    if os.path.exists(jl):
        with open(jl, encoding="utf-8") as jf:
            for i, line in enumerate(jf):
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                # Stable per-entry key; old entries without `id` get the same deterministic fallback.
                e["_id"] = e.get("id") or kg.source_entry_id(run_dir, len(entries), e)
                name = f"{len(entries) + 1:03d}-{slug(e['title'])}"
                e["_name"], e["_link"] = name, f"{rel_run}/notebook/entries/{name}"
                entries.append(e)
                known.add(e["_link"])
    # 2. Knowledge Graph (entities.jsonl and edges.jsonl)
    kg_ent_path = os.path.join(run_dir, "knowledge", "entities.jsonl")
    kg_edge_path = os.path.join(run_dir, "knowledge", "edges.jsonl")
    kg_entities = {}
    kg_edges = []
    if os.path.exists(kg_ent_path):
        with open(kg_ent_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        if obj.get("id"):
                            kg_entities[obj["id"]] = obj
                    except ValueError:
                        pass
    if os.path.exists(kg_edge_path):
        with open(kg_edge_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        kg_edges.append(json.loads(line))
                    except ValueError:
                        pass

    # Map entity id to vault note link
    ent_links = {}
    ent_dir = os.path.join(out_docs, rel_run, "knowledge", "entities")
    if kg_entities:
        os.makedirs(ent_dir, exist_ok=True)
        for eid, ent in kg_entities.items():
            eslug = re.sub(r"[^\w\-]+", "-", eid).strip("-").lower()
            elink = f"{rel_run}/knowledge/entities/{eslug}"
            ent_links[eid] = elink
            known.add(elink)

    # Render dedicated notes for KG entities
    for eid, ent in kg_entities.items():
        elink = ent_links[eid]
        eslug = os.path.basename(elink)
        out_edges = [ed for ed in kg_edges if ed.get("source") == eid]
        in_edges = [ed for ed in kg_edges if ed.get("target") == eid]

        fm_relations = []
        for ed in out_edges:
            tgt_link = ent_links.get(ed["target"], ed["target"])
            fm_relations.append(f"  - type: {ed['type']}\n    target: \"[[{tgt_link}]]\"")

        fm = [
            "---",
            f"id: \"{eid}\"",
            f"type: {ent.get('type')}",
            f"title: \"{ent.get('title', eid)}\"",
            f"created_at: \"{ent.get('created_at', '')}\"",
        ]
        if fm_relations:
            fm += ["relations:", *fm_relations]
        fm += ["---", "", f"# [{ent.get('type')}] {ent.get('title', eid)}", ""]

        if ent.get("body"):
            fm += [ent["body"].strip(), ""]

        if ent.get("properties"):
            fm += ["### Thuộc tính", f"```json\n{json.dumps(ent['properties'], ensure_ascii=False, indent=2)}\n```", ""]

        if out_edges:
            fm += ["### Quan hệ có kiểu (Typed Outgoing Edges)", ""]
            for ed in out_edges:
                tgt_ent = kg_entities.get(ed["target"], {"title": ed["target"]})
                tlink = ent_links.get(ed["target"], ed["target"])
                time_info = f" *(valid: {ed.get('valid_from') or '-'}..{ed.get('valid_to') or 'active'})*"
                fm.append(f"- **{ed['type']}** ➔ [[{tlink}|{tgt_ent.get('title')}]] ({ed['target']}){time_info}")
            fm.append("")

        if in_edges:
            fm += ["### Được liên kết bởi (Typed Incoming Edges)", ""]
            for ed in in_edges:
                src_ent = kg_entities.get(ed["source"], {"title": ed["source"]})
                slink = ent_links.get(ed["source"], ed["source"])
                time_info = f" *(valid: {ed.get('valid_from') or '-'}..{ed.get('valid_to') or 'active'})*"
                fm.append(f"- **{ed['type']}** 🠔 [[{slink}|{src_ent.get('title')}]] ({ed['source']}){time_info}")
            fm.append("")

        _write(os.path.join(ent_dir, f"{eslug}.md"), "\n".join(fm))

    # Render entries with typed edges if matched
    ed = os.path.join(out_docs, rel_run, "notebook", "entries")
    os.makedirs(ed, exist_ok=True)
    for i, e in enumerate(entries):
        tags = sorted({e["type"], *e.get("tags", [])})
        fm = ["---", f"type: {e['type']}", f"author: {e.get('author', '-')}", f"date: \"{e['ts']}\"",
              "tags: [" + ", ".join(t.replace(" ", "-") for t in tags) + "]"]

        # Check for related KG edges (match by stable source key, not title)
        entry_key_slug = kg.slug(e["_id"])
        matching_eids = [eid for eid in kg_entities if entry_key_slug in eid.lower()]
        related_edges = [ed for ed in kg_edges if any(ed.get("source") == m for m in matching_eids)]

        if related_edges:
            fm.append("relations:")
            for re_ed in related_edges:
                tlink = ent_links.get(re_ed["target"], re_ed["target"])
                fm.append(f"  - type: {re_ed['type']}\n    target: \"[[{tlink}]]\"")

        fm += ["---", "", f"# {e['title']}", "", e["body"].strip(), ""]
        if e.get("metrics"):
            fm += ["Metrics: " + ", ".join(f"`{k}`={v}" for k, v in e["metrics"].items()), ""]
        if e.get("refs"):
            fm += ["Refs: " + ", ".join(wikilink(r, known) for r in e["refs"]), ""]

        if related_edges:
            fm += ["", "### Quan hệ đồ thị tri thức (Typed Edges)", ""]
            for re_ed in related_edges:
                tgt_ent = kg_entities.get(re_ed["target"], {"title": re_ed["target"]})
                tlink = ent_links.get(re_ed["target"], re_ed["target"])
                time_str = f" *(valid: {re_ed.get('valid_from') or '-'}..{re_ed.get('valid_to') or 'active'})*"
                fm.append(f"- **{re_ed['type']}**: [[{tlink}|{tgt_ent.get('title')}]] ({re_ed['target']}){time_str}")

        nav = []
        if i > 0:
            nav.append("← " + f"[[{entries[i - 1]['_link']}]]")
        if i + 1 < len(entries):
            nav.append(f"[[{entries[i + 1]['_link']}]] →")
        if nav:
            fm += ["", " · ".join(nav), ""]
        _write(os.path.join(ed, e["_name"] + ".md"), "\n".join(fm))

    # Render dedicated Knowledge Graph summary page
    if kg_entities:
        kg_overview_link = f"{rel_run}/knowledge/KNOWLEDGE_GRAPH"
        known.add(kg_overview_link)
        kg_md = [
            f"# Đồ thị tri thức có kiểu (Typed Knowledge Graph): {rid}",
            "",
            f"Tổng số: **{len(kg_entities)}** thực thể (entities), **{len(kg_edges)}** cạnh có kiểu (typed edges).",
            "",
            "## Danh mục thực thể theo phân loại",
            "",
        ]
        by_type = collections.defaultdict(list)
        for eid, ent in kg_entities.items():
            by_type[ent.get("type")].append(ent)
        for kgt in ("Decision", "Incident", "Experiment", "Artifact", "Policy", "Task", "Person", "Run"):
            if kgt in by_type:
                kg_md.append(f"### {kgt} ({len(by_type[kgt])})")
                for ent in by_type[kgt]:
                    tlink = ent_links.get(ent["id"], ent["id"])
                    kg_md.append(f"- [[{tlink}|{ent.get('title')}]] (`{ent['id']}`)")
                kg_md.append("")

        kg_md += ["## Cạnh có kiểu (Typed Relations)", ""]
        by_edge_type = collections.defaultdict(list)
        for ed in kg_edges:
            by_edge_type[ed["type"]].append(ed)
        for et in ("supersedes", "caused", "decided_by", "approved_by", "evidenced_by", "evaluated_on", "uses", "depends_on"):
            if et in by_edge_type:
                kg_md.append(f"### {et} ({len(by_edge_type[et])})")
                for ed in by_edge_type[et]:
                    slink = ent_links.get(ed["source"], ed["source"])
                    tlink = ent_links.get(ed["target"], ed["target"])
                    sent = kg_entities.get(ed["source"], {"title": ed["source"]})
                    tent = kg_entities.get(ed["target"], {"title": ed["target"]})
                    time_str = f" *(valid: {ed.get('valid_from') or '-'}..{ed.get('valid_to') or 'active'})*"
                    kg_md.append(f"- [[{slink}|{sent.get('title')}]] ➔ **{et}** ➔ [[{tlink}|{tent.get('title')}]]{time_str}")
                kg_md.append("")

        _write(os.path.join(out_docs, rel_run, "knowledge", "KNOWLEDGE_GRAPH.md"), "\n".join(kg_md))

    groups = {}
    for e in entries:
        groups.setdefault(e["type"], []).append(e)
    moc = [f"# Bản đồ nội dung: {rid}", ""]

    if kg_entities:
        moc += [
            "## Đồ thị tri thức (Knowledge Graph)",
            f"- [[{rel_run}/knowledge/KNOWLEDGE_GRAPH|Tổng quan Knowledge Graph]] ({len(kg_entities)} thực thể, {len(kg_edges)} cạnh có kiểu)",
            "",
        ]

    for t, title in (("decision", "Quyết định"), ("insight", "Đúc kết"), ("experiment", "Thí nghiệm"),
                     ("error", "Lỗi / phân tích lỗi"), ("research", "Research"), ("gate", "Gate")):
        if t in groups:
            moc += [f"## {title}", *[f"- [[{e['_link']}]] — {e['title']}" for e in groups[t]], ""]
    rep = [c for c in copied if "/reports/" in c or c.endswith("/report")]
    art = [c for c in copied if c not in rep]
    if rep:
        moc += ["## Báo cáo", *[f"- [[{c}]]" for c in rep], ""]
    if art:
        moc += ["## Tài liệu / artifact", *[f"- [[{c}]]" for c in art], ""]
    _write(os.path.join(out_docs, rel_run, "INDEX.md"), "\n".join(moc))
    return rid, len(entries), len(copied)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", help="run dir (default: every runs/*/ that has a notebook or md)")
    ap.add_argument("--out", default=os.path.join(ROOT, "vault"))
    ap.add_argument("--graphify", help="path to the graphify executable (adds code/ via `export obsidian`)")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    marker = os.path.join(out, ".vault-generated")
    if os.path.exists(out):
        if not os.path.exists(marker):
            sys.exit(f"refusing to overwrite {out}: no .vault-generated marker (not made by this script)")
        shutil.rmtree(out)
    os.makedirs(out)
    _write(marker, "generated by scripts/obsidian_vault.py; safe to delete\n")
    docs = os.path.join(out, "docs")
    known = set()
    for f in TOP_DOCS:
        if os.path.exists(os.path.join(ROOT, f)):
            known.add(copy_md(os.path.join(ROOT, f), docs))
    for g in TOP_GLOBS:
        for p in glob.glob(os.path.join(ROOT, g)):
            known.add(copy_md(p, docs))
    runs = a.run or [d for d in glob.glob(os.path.join(ROOT, "runs", "*")) if os.path.isdir(d)]
    summary = [build_run(r, docs, known) for r in runs]
    idx = ["# Dự án — bản đồ tri thức", "", "Mở thư mục này như một vault trong Obsidian rồi dùng Graph view.", "",
           "## Quy tắc & workflow", "- [[AGENTS]] · [[README]]", "- Skills: " + ", ".join(f"[[{k}]]" for k in sorted(known) if k.startswith("skills/")),
           "- Roles: " + ", ".join(f"[[{k}]]" for k in sorted(known) if k.startswith("roles/")), "", "## Runs"]
    idx += [f"- [[runs/{rid}/INDEX|{rid}]] — {n} mục sổ thí nghiệm, {c} tài liệu" for rid, n, c in summary]
    if a.graphify:
        gx = os.path.abspath(a.graphify)
        if not os.path.exists(gx) and os.path.exists(gx + ".exe"):
            gx += ".exe"
        if not os.path.exists(gx):
            sys.exit(f"graphify executable not found: {a.graphify}")
        a.graphify = gx
        env = {k: v for k, v in os.environ.items() if k not in KEY_ENV}
        graph = os.path.join(ROOT, "graphify-out", "graph.json")
        if os.path.exists(graph):
            r = subprocess.run([a.graphify, "export", "obsidian", "--graph", graph, "--dir", os.path.join(out, "code")],
                               env=env, capture_output=True, text=True, encoding="utf-8")
            print((r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else "graphify export done")
            idx += ["", "## Code graph (Graphify)", "- Thư mục `code/` (xuất bằng `graphify export obsidian`, chỉ AST local)."]
        else:
            print("no graphify-out/graph.json: skipping code/ (run the ai-pipeline-graph steps first)")
    _write(os.path.join(out, "INDEX.md"), "\n".join(idx) + "\n")
    print(f"vault: {out}  ({len(known)} doc notes; runs: {', '.join(r for r, _, _ in summary) or '-'})")


if __name__ == "__main__":
    main()
