#!/usr/bin/env python3
"""Typed Knowledge Graph (KG) engineering toolkit for AI Pipeline.

Manages runs/<id>/knowledge/entities.jsonl and edges.jsonl in an append-only,
auditable, and temporally-aware format.

Node Types:
  Run, Task, Decision, Experiment, Artifact, Incident, Person, Policy

Edge Types (controlled enum with endpoint validation):
  depends_on, uses, evaluated_on, decided_by, approved_by, supersedes, caused, evidenced_by

Commands:
  kg.py init       <run_dir>
  kg.py add-entity <run_dir> --id <ID> --type <TYPE> --title <TITLE> [--body <BODY>] [--props <JSON>] [--created-at <TS>]
  kg.py add-edge   <run_dir> --source <SRC> --target <DST> --type <TYPE> [--valid-from <T>] [--valid-to <T>] [--recorded-at <T>] [--source-ref <REF>] [--confidence <FLOAT>] [--props <JSON>]
  kg.py validate   <run_dir>
  kg.py report     <run_dir>   (read-only: report wrong-type/dangling data, never modifies)
  kg.py neighbors  <run_dir> <node_id> [--direction in|out|both] [--edge-type <TYPE>] [--as-of <TS>]
  kg.py path       <run_dir> <src_id> <dst_id> [--max-hops <N>] [--edge-type <TYPE>] [--as-of <TS>]
  kg.py explain    <run_dir> <node_id> [--as-of <TS>]
  kg.py timeline   <run_dir> [--entity-type <TYPE>] [--as-of <TS>]
  kg.py backfill   <source_dir> [--out-dir <TARGET>]

Python standard library only.

Single write API for every KG writer (kg.py, settle_task.py, notebook.py, autonomy.py...):
  kg.upsert_entity(...), kg.upsert_artifact_ref(...), kg.add_edge_checked(...)
All of them validate enum/endpoint/node-existence/temporal/confidence and raise KgError.
"""

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import re
import sys

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

NODE_TYPES = ("Run", "Task", "Decision", "Experiment", "Artifact", "Incident", "Person", "Policy")
EDGE_TYPES = (
    "depends_on",
    "uses",
    "evaluated_on",
    "decided_by",
    "approved_by",
    "supersedes",
    "caused",
    "evidenced_by",
)

# Endpoint validation constraints: allowed source and target node types for each edge type
EDGE_ENDPOINT_CONSTRAINTS = {
    "depends_on": {
        "sources": {"Task", "Decision", "Run", "Policy"},
        "targets": {"Task", "Decision", "Artifact", "Policy", "Run"},
    },
    "uses": {
        "sources": {"Task", "Experiment", "Decision", "Artifact", "Run"},
        "targets": {"Artifact", "Policy", "Task"},
    },
    "evaluated_on": {
        "sources": {"Experiment", "Artifact", "Task"},
        "targets": {"Artifact"},
    },
    "decided_by": {
        "sources": {"Decision", "Policy"},
        "targets": {"Person"},
    },
    "approved_by": {
        "sources": {"Decision", "Policy", "Task", "Run"},
        "targets": {"Person"},
    },
    "supersedes": {
        "sources": {"Decision", "Artifact", "Policy", "Experiment"},
        "targets": {"Decision", "Artifact", "Policy", "Experiment"},
    },
    "caused": {
        "sources": {"Incident", "Decision", "Experiment", "Task"},
        "targets": {"Incident", "Decision", "Experiment", "Task"},
    },
    "evidenced_by": {
        "sources": {"Decision", "Incident", "Experiment", "Policy"},
        "targets": {"Experiment", "Artifact", "Incident"},
    },
}

ICONS = {
    "Run": "🚀",
    "Task": "📋",
    "Decision": "⚖️",
    "Experiment": "🧪",
    "Artifact": "📦",
    "Incident": "🚨",
    "Person": "👤",
    "Policy": "📜",
}

EDGE_ICONS = {
    "depends_on": "➔",
    "uses": "🔧",
    "evaluated_on": "🎯",
    "decided_by": "✍️",
    "approved_by": "✅",
    "supersedes": "🔄",
    "caused": "💥",
    "evidenced_by": "🔍",
}


def kg_dir(run_dir):
    d = os.path.abspath(run_dir)
    if os.path.basename(d) == "knowledge":
        return d
    return os.path.join(d, "knowledge")


def entities_path(run_dir):
    return os.path.join(kg_dir(run_dir), "entities.jsonl")


def edges_path(run_dir):
    return os.path.join(kg_dir(run_dir), "edges.jsonl")


def norm_ts(t):
    """Normalize timestamps to 'YYYY-MM-DD HH:MM:SS' string for deterministic comparison."""
    if not t:
        return None
    s = str(t).strip().replace("T", " ")
    # Match date only YYYY-MM-DD
    m1 = re.match(r"^(\d{4}-\d{2}-\d{2})$", s)
    if m1:
        return f"{m1.group(1)} 00:00:00"
    # Match YYYY-MM-DD HH:MM
    m2 = re.match(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})$", s)
    if m2:
        return f"{m2.group(1)}:00"
    # Match YYYY-MM-DD HH:MM:SS
    m3 = re.match(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", s)
    if m3:
        return m3.group(1)
    return s


def is_active_at(valid_from, valid_to, as_of_norm):
    if not as_of_norm:
        return True
    vf = norm_ts(valid_from)
    vt = norm_ts(valid_to)
    if vf and vf > as_of_norm:
        return False
    if vt and vt <= as_of_norm:
        return False
    return True


def read_entities(run_dir):
    p = entities_path(run_dir)
    if not os.path.exists(p):
        return {}
    entities = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                eid = record.get("id")
                if eid:
                    entities[eid] = record
            except ValueError:
                pass
    return entities


def read_edges(run_dir):
    p = edges_path(run_dir)
    if not os.path.exists(p):
        return []
    edges = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                edges.append(json.loads(line))
            except ValueError:
                pass
    return edges


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class KgError(ValueError):
    """Raised when an entity or edge violates the knowledge graph contract."""


def slug(text, n=64):
    """URL/ID-friendly lowercase slug (stable: same input -> same output)."""
    s = re.sub(r"[^\w\- ]+", "", str(text), flags=re.U).strip().replace(" ", "-")
    return (s[:n] or "entry").strip("-").lower()


def source_entry_id(run_dir, index, entry):
    """Stable source key for a notebook journal entry (run + ordinal + short content hash).

    Used by notebook.py and obsidian_vault.py so duplicate titles never collide."""
    run_name = os.path.basename(os.path.abspath(run_dir))
    raw = f"{entry.get('ts', '')}|{entry.get('title', '')}|{entry.get('body', '')}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{run_name}#{index:04d}-{digest}"


VERSION_RE = re.compile(r"(?:^|[-_/])(v?\d+(?:\.\d+)+)(?:$|[-_/])")


def normalize_ref_path(ref, root=None):
    """Return a forward-slash path relative to the project root when possible."""
    ref = str(ref).strip().replace("\\", "/")
    root = os.path.abspath(root or PROJECT_ROOT)
    if os.path.isabs(ref) or re.match(r"^[A-Za-z]:/", ref):
        try:
            ref = os.path.relpath(ref, root).replace("\\", "/")
        except ValueError:
            pass
    return ref


def extract_version(ref):
    """Best-effort version tag (e.g. v0.1) from an artifact reference path."""
    m = VERSION_RE.search(str(ref))
    return m.group(1) if m else None


def artifact_ref_id(ref, root=None):
    return f"artifact:{normalize_ref_path(ref, root)}"


def ensure_kg(run_dir):
    d = kg_dir(run_dir)
    os.makedirs(d, exist_ok=True)
    for p in (entities_path(run_dir), edges_path(run_dir)):
        if not os.path.exists(p):
            open(p, "w", encoding="utf-8").close()
    return d


def _append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def check_edge_endpoints(entities, source, target, edge_type):
    """Validate node existence and endpoint types. Returns problem strings.

    Each existing endpoint is checked independently, so a wrong endpoint TYPE is
    reported even when the other endpoint entity is missing (old-writer cleanup).
    """
    problems = []
    allowed = EDGE_ENDPOINT_CONSTRAINTS[edge_type]
    if source not in entities:
        problems.append(f"source '{source}' not found in entities.jsonl")
    elif entities[source].get("type") not in allowed["sources"]:
        problems.append(
            f"source '{source}' has type '{entities[source].get('type')}', "
            f"but {edge_type} allows sources: {sorted(allowed['sources'])}")
    if target not in entities:
        problems.append(f"target '{target}' not found in entities.jsonl")
    elif entities[target].get("type") not in allowed["targets"]:
        problems.append(
            f"target '{target}' has type '{entities[target].get('type')}', "
            f"but {edge_type} allows targets: {sorted(allowed['targets'])}")
    return problems


def upsert_entity(run_dir, entity_id, node_type, title, body="", properties=None, created_at=None):
    """Single write API for entities: validate then append (idempotent). Raises KgError."""
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise KgError("entity id must be a non-empty string")
    if node_type not in NODE_TYPES:
        raise KgError(f"invalid entity type '{node_type}'. Allowed: {', '.join(NODE_TYPES)}")
    if not title or not str(title).strip():
        raise KgError(f"entity '{entity_id}' must have a non-empty title")
    record = {
        "id": entity_id,
        "type": node_type,
        "title": title,
        "body": body or "",
        "properties": properties or {},
        "created_at": created_at or dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    old = read_entities(run_dir).get(entity_id)
    if old and all(old.get(k) == record.get(k) for k in ("id", "type", "title", "body", "properties")):
        return old
    ensure_kg(run_dir)
    _append_jsonl(entities_path(run_dir), record)
    return record


def upsert_artifact_ref(run_dir, ref, created_at=None, kind=None, title=None):
    """Create/refresh the Artifact entity for a reference path (notebook/settle_task)."""
    rel = normalize_ref_path(ref)
    aid = f"artifact:{rel}"
    props = {"path": rel}
    version = extract_version(rel)
    if version:
        props["version"] = version
    if kind:
        props["kind"] = kind
    upsert_entity(run_dir, aid, "Artifact", title or rel,
                  body=f"Artifact tại {rel}", properties=props, created_at=created_at)
    return aid


def add_edge_checked(run_dir, source, target, edge_type, valid_from=None, valid_to=None,
                     recorded_at=None, source_ref=None, confidence=1.0, properties=None,
                     allow_dangling=False):
    """Single write API for edges: validate enum/endpoints/temporal/confidence then append.

    Idempotent on (source, target, type). Raises KgError on any violation."""
    if edge_type not in EDGE_TYPES:
        raise KgError(f"invalid edge type '{edge_type}'. Allowed: {', '.join(EDGE_TYPES)}")
    problems = check_edge_endpoints(read_entities(run_dir), source, target, edge_type)
    if problems and not allow_dangling:
        raise KgError("edge rejected: " + "; ".join(problems)
                      + " (add the entity first; --allow-dangling only for backfills)")
    edge = {
        "source": source,
        "target": target,
        "type": edge_type,
        "valid_from": valid_from or None,
        "valid_to": valid_to or None,
        "recorded_at": recorded_at or dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source_ref": source_ref or None,
        "confidence": float(confidence) if confidence is not None else 1.0,
        "properties": properties or {},
    }
    vf, vt = norm_ts(edge["valid_from"]), norm_ts(edge["valid_to"])
    if vf and vt and vf > vt:
        raise KgError(f"edge rejected: valid_from ('{vf}') is after valid_to ('{vt}')")
    if not (0.0 <= edge["confidence"] <= 1.0):
        raise KgError(f"edge rejected: confidence '{edge['confidence']}' not in [0.0, 1.0]")
    if problems:
        print("Warning (allowed by allow_dangling): " + "; ".join(problems), file=sys.stderr)
    for existing in read_edges(run_dir):
        if (existing.get("source"), existing.get("target"), existing.get("type")) == (source, target, edge_type):
            return existing
    ensure_kg(run_dir)
    _append_jsonl(edges_path(run_dir), edge)
    return edge


def collect_validation(run_dir):
    """Shared validation used by `validate` (exit 1 on error) and `report` (report-only)."""
    entities = read_entities(run_dir)
    edges = read_edges(run_dir)
    errors, warnings = [], []
    for eid, ent in entities.items():
        if ent.get("type") not in NODE_TYPES:
            errors.append(f"Entity '{eid}': invalid type '{ent.get('type')}'. Allowed: {NODE_TYPES}")
        if not ent.get("title"):
            warnings.append(f"Entity '{eid}': title is empty")
    for i, edge in enumerate(edges):
        src, dst, etype = edge.get("source"), edge.get("target"), edge.get("type")
        if not src or not dst:
            errors.append(f"Edge #{i}: missing source or target ({edge})")
            continue
        if etype not in EDGE_TYPES:
            errors.append(f"Edge #{i}: invalid edge type '{etype}'. Allowed: {EDGE_TYPES}")
            continue
        for problem in check_edge_endpoints(entities, src, dst, etype):
            errors.append(f"Edge #{i} ({etype}): {problem}")
        vf, vt = norm_ts(edge.get("valid_from")), norm_ts(edge.get("valid_to"))
        if vf and vt and vf > vt:
            errors.append(f"Edge #{i}: valid_from ('{vf}') is after valid_to ('{vt}')")
        conf = edge.get("confidence")
        if conf is not None and not (0.0 <= float(conf) <= 1.0):
            errors.append(f"Edge #{i}: confidence '{conf}' not in [0.0, 1.0]")
    return entities, edges, errors, warnings


def cmd_init(a):
    d = kg_dir(a.run_dir)
    os.makedirs(d, exist_ok=True)
    ep = entities_path(a.run_dir)
    edp = edges_path(a.run_dir)
    if not os.path.exists(ep):
        with open(ep, "w", encoding="utf-8") as f:
            pass
    if not os.path.exists(edp):
        with open(edp, "w", encoding="utf-8") as f:
            pass
    print(f"Knowledge graph initialized: {d}")


def cmd_add_entity(a):
    props = json.loads(a.props) if a.props else {}
    try:
        record = upsert_entity(a.run_dir, a.id, a.type, a.title, a.body, props, a.created_at)
    except KgError as ex:
        sys.exit(str(ex))
    print(f"Upserted entity [{record['type']}] {record['id']} ('{record['title']}')")


def cmd_add_edge(a):
    props = json.loads(a.props) if a.props else {}
    try:
        edge = add_edge_checked(a.run_dir, a.source, a.target, a.type, a.valid_from, a.valid_to,
                                a.recorded_at, a.source_ref, a.confidence, props,
                                getattr(a, "allow_dangling", False))
    except KgError as ex:
        sys.exit(str(ex))
    print(f"Added edge: {edge['source']} --[{edge['type']}]--> {edge['target']}")


def _print_kg_summary(run_dir, entities, edges, errors, warnings):
    print(f"=== Knowledge Graph Validation: {run_dir} ===")
    print(f"Total entities: {len(entities)}")
    print(f"Total edges:    {len(edges)}")
    type_counts = collections.Counter(e.get("type") for e in entities.values())
    print("Entity breakdown: " + ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items())))
    edge_counts = collections.Counter(e.get("type") for e in edges)
    print("Edge breakdown:   " + ", ".join(f"{k}={v}" for k, v in sorted(edge_counts.items())))
    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  [!] {w}")
    return type_counts, edge_counts


def cmd_validate(a):
    entities, edges, errors, warnings = collect_validation(a.run_dir)
    _print_kg_summary(a.run_dir, entities, edges, errors, warnings)
    if errors:
        print(f"\nERRORS ({len(errors)}):", file=sys.stderr)
        for err in errors:
            print(f"  [X] {err}", file=sys.stderr)
        sys.exit(1)
    print("\nResult: VALID (enum, endpoints, temporal order, confidence passed).")


def cmd_report(a):
    """Report-only (không sửa) các cạnh/thực thể sai để người dùng dọn dữ liệu writer cũ."""
    entities, edges, errors, warnings = collect_validation(a.run_dir)
    _print_kg_summary(a.run_dir, entities, edges, errors, warnings)
    print(f"\nBáo cáo (chỉ đọc, không tự sửa): {len(errors)} lỗi, {len(warnings)} cảnh báo.")
    if errors:
        print(f"\nVấn đề cần dọn ({len(errors)}):")
        for err in errors:
            print(f"  [X] {err}")
    if not errors:
        print("Không phát hiện cạnh/thực thể sai kiểu hoặc lơ lửng.")
    print("\nKết quả: REPORT-ONLY (không thay đổi file nào).")


def cmd_neighbors(a):
    entities = read_entities(a.run_dir)
    edges = read_edges(a.run_dir)
    node_id = a.node_id
    as_of = norm_ts(a.as_of) if a.as_of else None

    if node_id not in entities:
        print(f"Node '{node_id}' not found in entities. Checking edges anyway...", file=sys.stderr)

    ent = entities.get(node_id, {"type": "Unknown", "title": node_id})
    icon = ICONS.get(ent.get("type"), "•")
    print(f"{icon} {node_id} ({ent.get('type')}) - \"{ent.get('title')}\"")
    if as_of:
        print(f"  [Filter as-of: {a.as_of}]")

    out_edges = []
    in_edges = []

    for e in edges:
        if a.edge_type and e.get("type") != a.edge_type:
            continue
        if as_of and not is_active_at(e.get("valid_from"), e.get("valid_to"), as_of):
            continue
        if e.get("source") == node_id:
            out_edges.append(e)
        if e.get("target") == node_id:
            in_edges.append(e)

    if a.direction in ("out", "both"):
        print(f"\nOutgoing edges ({len(out_edges)}):")
        if not out_edges:
            print("  (none)")
        for e in out_edges:
            tgt = entities.get(e["target"], {"type": "Unknown", "title": e["target"]})
            e_icon = EDGE_ICONS.get(e["type"], "->")
            t_icon = ICONS.get(tgt.get("type"), "•")
            time_str = f" [valid: {e.get('valid_from') or '-'} .. {e.get('valid_to') or 'active'}]"
            print(f"  {e_icon} --{e['type']}--> {t_icon} {e['target']} ({tgt.get('type')}): {tgt.get('title')}{time_str}")

    if a.direction in ("in", "both"):
        print(f"\nIncoming edges ({len(in_edges)}):")
        if not in_edges:
            print("  (none)")
        for e in in_edges:
            src = entities.get(e["source"], {"type": "Unknown", "title": e["source"]})
            e_icon = EDGE_ICONS.get(e["type"], "->")
            s_icon = ICONS.get(src.get("type"), "•")
            time_str = f" [valid: {e.get('valid_from') or '-'} .. {e.get('valid_to') or 'active'}]"
            print(f"  {e_icon} <--{e['type']}-- {s_icon} {e['source']} ({src.get('type')}): {src.get('title')}{time_str}")


def cmd_path(a):
    entities = read_entities(a.run_dir)
    edges = read_edges(a.run_dir)
    src_id = a.src_id
    dst_id = a.dst_id
    as_of = norm_ts(a.as_of) if a.as_of else None
    max_hops = a.max_hops

    if src_id not in entities:
        print(f"Warning: src '{src_id}' not in entities", file=sys.stderr)
    if dst_id not in entities:
        print(f"Warning: dst '{dst_id}' not in entities", file=sys.stderr)

    # Filter active edges
    adj = collections.defaultdict(list)
    for e in edges:
        if a.edge_type and e.get("type") != a.edge_type:
            continue
        if as_of and not is_active_at(e.get("valid_from"), e.get("valid_to"), as_of):
            continue
        adj[e["source"]].append(e)

    # BFS to find shortest path
    queue = collections.deque([(src_id, [])])
    visited = {src_id}
    found_paths = []

    while queue:
        curr, path = queue.popleft()
        if curr == dst_id:
            found_paths.append(path)
            break
        if len(path) >= max_hops:
            continue
        for edge in adj.get(curr, []):
            nxt = edge["target"]
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, path + [edge]))

    print(f"=== Path Search: {src_id} -> {dst_id} ===")
    if as_of:
        print(f"As of: {a.as_of}")

    if not found_paths:
        print(f"No path found within {max_hops} hops.")
        return

    path = found_paths[0]
    print(f"Found path in {len(path)} hops:\n")
    cur = src_id
    c_ent = entities.get(cur, {"type": "Unknown", "title": cur})
    print(f"[{c_ent.get('type')}] {cur} (\"{c_ent.get('title')}\")")

    for i, edge in enumerate(path, 1):
        nxt = edge["target"]
        n_ent = entities.get(nxt, {"type": "Unknown", "title": nxt})
        e_icon = EDGE_ICONS.get(edge["type"], "➔")
        conf_str = f", conf={edge.get('confidence')}" if edge.get("confidence") != 1.0 else ""
        time_str = f"valid: {edge.get('valid_from') or '-'}..{edge.get('valid_to') or 'active'}"
        ref_str = f", ref: {edge.get('source_ref')}" if edge.get("source_ref") else ""
        print(f"   |")
        print(f"   +--({e_icon} {edge['type']} [{time_str}{conf_str}{ref_str}])")
        print(f"   v")
        print(f"[{n_ent.get('type')}] {nxt} (\"{n_ent.get('title')}\")")


def cmd_explain(a):
    entities = read_entities(a.run_dir)
    edges = read_edges(a.run_dir)
    node_id = a.node_id
    as_of = norm_ts(a.as_of) if a.as_of else None

    if node_id not in entities:
        sys.exit(f"Node '{node_id}' not found in entities.jsonl")

    ent = entities[node_id]
    icon = ICONS.get(ent["type"], "•")

    print("=" * 70)
    print(f"{icon} EXPLAIN: [{ent['type']}] {node_id}")
    print(f"Tiêu đề:       {ent['title']}")
    if ent.get("created_at"):
        print(f"Thời điểm tạo: {ent['created_at']}")
    if ent.get("properties"):
        print(f"Thuộc tính:    {json.dumps(ent['properties'], ensure_ascii=False)}")
    if ent.get("body"):
        print(f"\nNội dung chi tiết:\n{ent['body'].strip()}\n")

    # Group incoming and outgoing
    out_by_type = collections.defaultdict(list)
    in_by_type = collections.defaultdict(list)

    is_superseded = False
    superseded_by = []

    for e in edges:
        active = is_active_at(e.get("valid_from"), e.get("valid_to"), as_of)
        if e["source"] == node_id:
            out_by_type[e["type"]].append((e, active))
        if e["target"] == node_id:
            in_by_type[e["type"]].append((e, active))
            if e["type"] == "supersedes" and active:
                is_superseded = True
                superseded_by.append(e["source"])

    print("-" * 70)
    print("TRẠNG THÁI HIỆU LỰC (TEMPORAL STATUS):")
    if as_of:
        print(f"- Đánh giá tại mốc 'as of': {a.as_of}")
    if is_superseded:
        print(f"- ⚠️  BỊ THAY THẾ (SUPERSEDED) bởi: {', '.join(superseded_by)}")
    else:
        print("- ✅ Đang có hiệu lực (ACTIVE / CURRENT)")

    print("\nNGUYÊN NHÂN & CĂN CỨ (PROVENANCE & EVIDENCE):")
    # decided_by
    if "decided_by" in out_by_type:
        for e, act in out_by_type["decided_by"]:
            p = entities.get(e["target"], {"title": e["target"]})
            print(f"  • Quyết định bởi (decided_by): {e['target']} ({p.get('title')})")
    # approved_by
    if "approved_by" in out_by_type:
        for e, act in out_by_type["approved_by"]:
            p = entities.get(e["target"], {"title": e["target"]})
            print(f"  • Phê duyệt bởi (approved_by): {e['target']} ({p.get('title')})")
    # evidenced_by
    if "evidenced_by" in out_by_type:
        for e, act in out_by_type["evidenced_by"]:
            ev = entities.get(e["target"], {"title": e["target"], "type": "Artifact"})
            ref = f" (ref: {e.get('source_ref')})" if e.get("source_ref") else ""
            print(f"  • Bằng chứng thực nghiệm (evidenced_by): [{ev.get('type')}] {e['target']} - {ev.get('title')}{ref}")
    # caused by what?
    if "caused" in in_by_type:
        for e, act in in_by_type["caused"]:
            c = entities.get(e["source"], {"title": e["source"], "type": "Incident"})
            print(f"  • Bị gây ra bởi (caused by): [{c.get('type')}] {e['source']} - {c.get('title')}")

    print("\nQUAN HỆ TIẾN HÓA & THAY THẾ (EVOLUTION & SUPERSEDING):")
    if "supersedes" in out_by_type:
        for e, act in out_by_type["supersedes"]:
            old = entities.get(e["target"], {"title": e["target"], "type": "Decision"})
            print(f"  • Thay thế quyết định cũ (supersedes): [{old.get('type')}] {e['target']} - {old.get('title')}")
    if "supersedes" in in_by_type:
        for e, act in in_by_type["supersedes"]:
            new_e = entities.get(e["source"], {"title": e["source"], "type": "Decision"})
            act_str = " (đã kích hoạt)" if act else " (chưa có hiệu lực ở mốc này)"
            print(f"  • Bị thay thế bởi (superseded by): [{new_e.get('type')}] {e['source']} - {new_e.get('title')}{act_str}")

    print("\nHỆ QUẢ & TÁC ĐỘNG (CONSEQUENCES & USAGE):")
    if "caused" in out_by_type:
        for e, act in out_by_type["caused"]:
            tgt = entities.get(e["target"], {"title": e["target"], "type": "Incident"})
            print(f"  • Gây ra sự cố/hệ quả (caused): [{tgt.get('type')}] {e['target']} - {tgt.get('title')}")
    if "depends_on" in in_by_type:
        for e, act in in_by_type["depends_on"]:
            dep = entities.get(e["source"], {"title": e["source"], "type": "Task"})
            print(f"  • Thành phần phụ thuộc vào đây: [{dep.get('type')}] {e['source']} - {dep.get('title')}")
    if "uses" in in_by_type:
        for e, act in in_by_type["uses"]:
            user = entities.get(e["source"], {"title": e["source"], "type": "Task"})
            print(f"  • Được sử dụng bởi: [{user.get('type')}] {e['source']} - {user.get('title')}")
    print("=" * 70)


def cmd_timeline(a):
    entities = read_entities(a.run_dir)
    edges = read_edges(a.run_dir)
    as_of = norm_ts(a.as_of) if a.as_of else None

    events = []
    for eid, ent in entities.items():
        if a.entity_type and ent.get("type") != a.entity_type:
            continue
        ts = norm_ts(ent.get("created_at")) or "1970-01-01 00:00:00"
        if as_of and ts > as_of:
            continue
        events.append((ts, "entity", ent))

    for edge in edges:
        ts = norm_ts(edge.get("valid_from") or edge.get("recorded_at")) or "1970-01-01 00:00:00"
        if as_of and not is_active_at(edge.get("valid_from"), edge.get("valid_to"), as_of):
            continue
        events.append((ts, "edge", edge))

    events.sort(key=lambda x: x[0])

    print(f"=== Timeline Knowledge Graph: {a.run_dir} ===")
    if as_of:
        print(f"As of: {a.as_of}")
    print(f"Total chronological items: {len(events)}\n")

    for ts, kind, obj in events:
        if kind == "entity":
            icon = ICONS.get(obj.get("type"), "•")
            print(f"[{ts[:16]}] {icon} [{obj['type']}] {obj['id']}: {obj.get('title')}")
        else:
            e_icon = EDGE_ICONS.get(obj.get("type"), "➔")
            s = obj["source"]
            t = obj["target"]
            vf = obj.get("valid_from") or "-"
            vt = obj.get("valid_to") or "active"
            print(f"[{ts[:16]}]    {e_icon} ({obj['type']}) {s} ➔ {t} [valid: {vf}..{vt}]")


def cmd_backfill(a):
    """Backfill a rich typed knowledge graph from an existing run (e.g. runs/timesheet-ocr)."""
    src_dir = os.path.abspath(a.source_dir)
    target_dir = os.path.abspath(a.out_dir) if a.out_dir else src_dir

    kd = kg_dir(target_dir)
    os.makedirs(kd, exist_ok=True)

    entities = []
    edges = []

    # 1. Base Run Entity
    run_name = os.path.basename(src_dir)
    entities.append({
        "id": f"run:{run_name}",
        "type": "Run",
        "title": f"Pipeline Run: {run_name}",
        "body": f"AI pipeline execution run for {run_name}",
        "properties": {"run_id": run_name},
        "created_at": "2026-10-03 20:00",
    })

    # Standard Person entities
    persons = [
        ("person:user", "Người dùng / Product Owner / Human Supervisor"),
        ("person:coordinator", "Orca Run Coordinator"),
        ("person:data-analyst", "Data Analyst Agent"),
        ("person:requirements-analyst", "Requirements Analyst Agent"),
        ("person:researcher", "Researcher Agent"),
        ("person:feasibility-analyst", "Feasibility / Ceiling Analyst Agent"),
        ("person:model-proposer", "Model Proposer Agent"),
        ("person:critic", "Critic / Debater Agent"),
        ("person:architect", "Architect / Judge Agent"),
        ("person:module-dev", "Module Developer Agent"),
    ]
    for pid, ptitle in persons:
        entities.append({
            "id": pid,
            "type": "Person",
            "title": ptitle,
            "body": "",
            "properties": {"alias": pid.replace("person:", "")},
            "created_at": "2026-10-03 20:00",
        })

    # Standard Policy entities
    entities.append({
        "id": "policy:sandbox-container-only",
        "type": "Policy",
        "title": "Chính sách Sandbox: Mọi thực thi trong container",
        "body": "Quy tắc bắt buộc ai-pipeline-sandbox: mọi việc chạy/benchmark/train/export chỉ diễn ra trong container server; cấm cài/chạy host.",
        "properties": {"scope": "global", "enforced": True},
        "created_at": "2026-10-03 20:00",
    })
    entities.append({
        "id": "policy:gpu-vram-limit-8gb",
        "type": "Policy",
        "title": "Chính sách Giới hạn VRAM GPU <= 8GB",
        "body": "Server A6000 dùng chung với production khác: chỉ sử dụng <= 8GB VRAM dưới khóa flock.",
        "properties": {"cap_mib": 8192},
        "created_at": "2026-10-03 20:00",
    })

    # 2. Key Decisions from decisions.md & notebook
    decisions_data = [
        {
            "id": "decision:G1-target-raw",
            "title": "G1: Mục tiêu >= 99% Exact Match theo từng field",
            "body": "Chỉ có 100 trang synthetic ds-v1, không có real nhãn. Đặt mục tiêu >=99% exact match chuẩn hóa từng field.",
            "author": "person:coordinator",
            "approver": "person:user",
            "created_at": "2026-10-03 20:17",
            "valid_from": "2026-10-03 20:17",
            "valid_to": "2026-10-03 20:58",
            "source_ref": "runs/timesheet-ocr/decisions.md:3-7",
        },
        {
            "id": "decision:break-time-norm-min",
            "title": "Chuẩn hóa break_time theo số phút (phút thay vì text thô)",
            "body": "A1 phát hiện nhãn KIE ngẫu nhiên 1h/60/1:00 cho cùng giá trị 60 phút (trần thô 78.97%). Đổi quy tắc so khớp sang số phút HH:MM.",
            "author": "person:data-analyst",
            "approver": "person:user",
            "created_at": "2026-10-03 20:25",
            "valid_from": "2026-10-03 20:58",
            "valid_to": None,
            "source_ref": "runs/timesheet-ocr/decisions.md:14,24",
            "supersedes": "decision:G1-target-raw",
            "evidence": "exp:A1-raw-break-analysis",
        },
        {
            "id": "decision:G2-plan-arch-v0.1",
            "title": "G2: Duyệt kiến trúc arch-v0.1 và tiêu chí đạt",
            "body": "Duyệt pipeline PicoDet-S + PP-OCRv5 mobile + norm-v1; duyệt 7-fold OOF 504 dòng; đạt là quan sát >=99% kèm CI.",
            "author": "person:architect",
            "approver": "person:user",
            "created_at": "2026-10-03 20:58",
            "valid_from": "2026-10-03 20:58",
            "valid_to": None,
            "source_ref": "runs/timesheet-ocr/decisions.md:22-26",
            "uses": "policy:gpu-vram-limit-8gb",
        },
        {
            "id": "decision:allow-host-benchmark",
            "title": "Cho phép benchmark ONNX local trên host (quyết định ban đầu)",
            "body": "Module RT tiến hành micro-benchmark ONNX và cài các thư viện vào host Python.",
            "author": "person:module-dev",
            "approver": None,
            "created_at": "2026-10-03 21:12",
            "valid_from": "2026-10-03 21:12",
            "valid_to": "2026-10-03 22:02",
            "source_ref": "runs/timesheet-ocr/decisions.md:27-29",
        },
        {
            "id": "decision:remove-host-packages",
            "title": "Gỡ 6 gói host và bắt buộc đo latency trong container server",
            "body": "Người dùng duyệt gỡ onnx, onnxruntime, paddle2onnx, rapidocr, flatbuffers, polygraphy khỏi host. Chuyển toàn bộ đo đạc vào container server.",
            "author": "person:coordinator",
            "approver": "person:user",
            "created_at": "2026-10-03 22:02",
            "valid_from": "2026-10-03 22:02",
            "valid_to": None,
            "source_ref": "runs/timesheet-ocr/decisions.md:31",
            "supersedes": "decision:allow-host-benchmark",
            "evidence": "incident:host-package-install",
            "uses": "policy:sandbox-container-only",
        },
        {
            "id": "decision:C1-continue-and-add-services",
            "title": "C1: Giữ mục tiêu 99% làm mốc, tiếp tục build và thêm 2 service",
            "body": "Probe B0 cho thấy mục tiêu 99% vượt ceiling lạc quan do sàn mơ hồ. Người dùng chọn tiếp tục build đầy đủ và thêm task SV1 (LitServe) + SV2 (FastAPI KIE).",
            "author": "person:user",
            "approver": "person:user",
            "created_at": "2026-10-04 07:26",
            "valid_from": "2026-10-04 07:26",
            "valid_to": None,
            "source_ref": "runs/timesheet-ocr/decisions.md:33-36",
            "evidence": "exp:B0-probe-ceiling",
        },
        {
            "id": "decision:add-d2-synthetic-gen",
            "title": "D2: Thêm công cụ sinh dữ liệu ds-v2-synth và train lại MH2",
            "body": "Người dùng chọn xây tool sinh dữ liệu ngay, song song. Bổ sung D2 và MH2 vào plan để cải thiện rec-hw.",
            "author": "person:user",
            "approver": "person:user",
            "created_at": "2026-10-04 08:00",
            "valid_from": "2026-10-04 08:00",
            "valid_to": "2026-10-04 10:15",
            "source_ref": "runs/timesheet-ocr/decisions.md:49-51",
        },
        {
            "id": "decision:cancel-mh2-keep-rec-v0.1",
            "title": "Hủy bỏ MH2, giữ nguyên rec-hw-v0.1 và dừng sinh data synthetic",
            "body": "Ablation D2 chứng minh synth ghép/augment KHÔNG giúp rec-hw (giả thuyết bị bác bỏ). Hủy MH2 khỏi plan, X1 và SV1 chạy trên rec-hw v0.1.",
            "author": "person:coordinator",
            "approver": "person:user",
            "created_at": "2026-10-04 10:15",
            "valid_from": "2026-10-04 10:15",
            "valid_to": None,
            "source_ref": "runs/timesheet-ocr/decisions.md:56-57",
            "supersedes": "decision:add-d2-synthetic-gen",
            "evidence": "exp:D2-ablation-synth",
        },
    ]

    for d in decisions_data:
        entities.append({
            "id": d["id"],
            "type": "Decision",
            "title": d["title"],
            "body": d["body"],
            "properties": {"source_ref": d.get("source_ref")},
            "created_at": d["created_at"],
        })
        if d.get("author"):
            edges.append({
                "source": d["id"],
                "target": d["author"],
                "type": "decided_by",
                "valid_from": d["valid_from"],
                "valid_to": d["valid_to"],
                "recorded_at": d["created_at"],
                "source_ref": d.get("source_ref"),
                "confidence": 1.0,
            })
        if d.get("approver"):
            edges.append({
                "source": d["id"],
                "target": d["approver"],
                "type": "approved_by",
                "valid_from": d["valid_from"],
                "valid_to": d["valid_to"],
                "recorded_at": d["created_at"],
                "source_ref": d.get("source_ref"),
                "confidence": 1.0,
            })
        if d.get("supersedes"):
            edges.append({
                "source": d["id"],
                "target": d["supersedes"],
                "type": "supersedes",
                "valid_from": d["valid_from"],
                "valid_to": None,
                "recorded_at": d["created_at"],
                "source_ref": d.get("source_ref"),
                "confidence": 1.0,
            })
        if d.get("evidence"):
            edges.append({
                "source": d["id"],
                "target": d["evidence"],
                "type": "evidenced_by",
                "valid_from": d["valid_from"],
                "valid_to": d["valid_to"],
                "recorded_at": d["created_at"],
                "source_ref": d.get("source_ref"),
                "confidence": 1.0,
            })
        if d.get("uses"):
            edges.append({
                "source": d["id"],
                "target": d["uses"],
                "type": "uses",
                "valid_from": d["valid_from"],
                "valid_to": d["valid_to"],
                "recorded_at": d["created_at"],
                "source_ref": d.get("source_ref"),
                "confidence": 1.0,
            })

    # 3. Artifact Entities
    artifacts_data = [
        ("artifact:dataset:ds-v1", "Tập dữ liệu 100 trang synthetic gốc", "dataset", "ds-v1"),
        ("artifact:dataset:ds-v1.1", "Tập dữ liệu ds-v1.1 tách phần ngày và chuẩn hóa break_min", "dataset", "ds-v1.1"),
        ("artifact:model:probe-rec-ppocrv5m", "Checkpoint probe PP-OCRv5 Mobile Rec", "model", "probe-rec-v0.1"),
        ("artifact:model:rec-hw-v0.1", "Model chính thức nhận dạng chữ tay rec-hw-v0.1", "model", "rec-hw-v0.1"),
        ("artifact:model:layout-picodet-v0.1", "Model định vị layout PicoDet-S v0.1", "model", "layout-v0.1"),
        ("artifact:report:C1-ceiling", "Báo cáo ước lượng trần khả thi C1 và đọc mù", "report", "ceiling-C1"),
        ("artifact:eval:I1-test-eval", "Kết quả đánh giá trên tập test cố định 142 dòng", "eval", "eval-I1"),
    ]
    for aid, atitle, akind, aversion in artifacts_data:
        entities.append({
            "id": aid,
            "type": "Artifact",
            "title": atitle,
            "body": f"Artifact {akind} version {aversion}",
            "properties": {"kind": akind, "version": aversion},
            "created_at": "2026-10-03 20:00",
        })

    # Artifact relationships: ds-v1.1 supersedes ds-v1
    edges.append({
        "source": "artifact:dataset:ds-v1.1",
        "target": "artifact:dataset:ds-v1",
        "type": "supersedes",
        "valid_from": "2026-10-03 21:11",
        "valid_to": None,
        "recorded_at": "2026-10-03 21:11",
        "source_ref": "runs/timesheet-ocr/notebook/journal.jsonl:24",
        "confidence": 1.0,
    })

    # 4. Experiment Entities
    exps_data = [
        {
            "id": "exp:A1-raw-break-analysis",
            "title": "A1: Khảo sát nhãn break_time thô trên 504 dòng ds-v1",
            "body": "So sánh visible text với KIE gt: giá trị 60 phút có 3 biểu diễn ngẫu nhiên (1h, 60, 1:00) làm trần exact match chỉ đạt 78.97%.",
            "created_at": "2026-10-03 20:25",
            "eval_on": "artifact:dataset:ds-v1",
        },
        {
            "id": "exp:B0-probe-ceiling",
            "title": "B0: Learning curve + capacity probe + đọc mù 150 ô",
            "body": "Đo EM val theo crop GT, fit learning curve b=0.31; người đọc mù bất đồng 18.7% cho thấy sàn mơ hồ cao, trần lạc quan start 94%, end 95%.",
            "created_at": "2026-10-03 23:24",
            "eval_on": "artifact:dataset:ds-v1.1",
        },
        {
            "id": "exp:D2-ablation-synth",
            "title": "D2: Thử nghiệm ablation dữ liệu sinh ds-v2-synth trên val",
            "body": "Dữ liệu sinh ghép/augment không cải thiện rec-hw (kết quả âm). Giả thuyết sinh dữ liệu bị bác bỏ.",
            "created_at": "2026-10-04 10:00",
            "eval_on": "artifact:dataset:ds-v1.1",
        },
        {
            "id": "exp:MH-train-rec-hw",
            "title": "MH: Huấn luyện rec-hw-v0.1 trên ds-v1.1 với GPU flock",
            "body": "Fine-tune PP-OCRv5 mobile với 3072 mẫu train, cosine LR 2e-4, đạt loss 2.34 trong giới hạn VRAM 8GB.",
            "created_at": "2026-10-04 07:51",
            "eval_on": "artifact:dataset:ds-v1.1",
        },
    ]
    for ex in exps_data:
        entities.append({
            "id": ex["id"],
            "type": "Experiment",
            "title": ex["title"],
            "body": ex["body"],
            "properties": {},
            "created_at": ex["created_at"],
        })
        if ex.get("eval_on"):
            edges.append({
                "source": ex["id"],
                "target": ex["eval_on"],
                "type": "evaluated_on",
                "valid_from": ex["created_at"],
                "valid_to": None,
                "recorded_at": ex["created_at"],
                "confidence": 1.0,
            })

    # 5. Incident Entities
    incidents_data = [
        {
            "id": "incident:host-package-install",
            "title": "Cài 6 gói pip vào Python host trước khi có quy tắc sandbox",
            "body": "Workers đã cài onnx, onnxruntime, paddle2onnx, rapidocr, flatbuffers, polygraphy vào máy chủ cục bộ trái với quy tắc sandbox.",
            "created_at": "2026-10-03 22:01",
            "source_ref": "runs/timesheet-ocr/decisions.md:27-31",
            "caused_by": "task:RT",
            "caused_decision": "decision:remove-host-packages",
        },
        {
            "id": "incident:paddle2onnx-pip-auto-install",
            "title": "paddle2onnx tự ý pip install onnx_graphsurgeon trong container",
            "body": "Khi export ONNX, optimizer của paddle2onnx tự gọi pip install package ngoài danh sách phê duyệt trong container aipipeline-timesheet-train.",
            "created_at": "2026-10-04 07:54",
            "source_ref": "runs/timesheet-ocr/decisions.md:46-47",
            "caused_by": "task:MH",
        },
        {
            "id": "incident:container-numpy-drift",
            "title": "Lệch phiên bản thư viện NumPy 1.26.4 -> 2.2.6 trong container train",
            "body": "Container aipipeline-timesheet-train bị thay đổi numpy lên 2.2.6 làm PaddleDetection export_model.py bị lỗi import imgaug.",
            "created_at": "2026-10-04 07:55",
            "source_ref": "runs/timesheet-ocr/decisions.md:48,50",
            "caused_by": "task:MH",
        },
    ]
    for inc in incidents_data:
        entities.append({
            "id": inc["id"],
            "type": "Incident",
            "title": inc["title"],
            "body": inc["body"],
            "properties": {"source_ref": inc.get("source_ref")},
            "created_at": inc["created_at"],
        })
        if inc.get("caused_by"):
            edges.append({
                "source": inc["caused_by"],
                "target": inc["id"],
                "type": "caused",
                "valid_from": inc["created_at"],
                "valid_to": None,
                "recorded_at": inc["created_at"],
                "source_ref": inc.get("source_ref"),
                "confidence": 1.0,
            })
        if inc.get("caused_decision"):
            edges.append({
                "source": inc["id"],
                "target": inc["caused_decision"],
                "type": "caused",
                "valid_from": inc["created_at"],
                "valid_to": None,
                "recorded_at": inc["created_at"],
                "source_ref": inc.get("source_ref"),
                "confidence": 1.0,
            })

    # 6. Task Entities from plan
    tasks_data = [
        ("task:G1", "Gate G1: Làm rõ spec", []),
        ("task:A1", "A1: Phân tích dữ liệu & split ds-v1", ["task:G1"]),
        ("task:G2", "Gate G2: Duyệt kế hoạch kiến trúc", ["task:A1"]),
        ("task:RT", "RT: Micro-benchmark ONNX runtime & layout crop", ["task:G2"]),
        ("task:D1", "D1: Dựng dataset ds-v1.1", ["task:G2"]),
        ("task:B0", "B0: Probe khả thi C1 và đọc mù", ["task:D1"]),
        ("task:MH", "MH: Huấn luyện rec-hw-v0.1", ["task:B0"]),
        ("task:ML", "ML: Huấn luyện layout-v0.1", ["task:B0"]),
        ("task:D2", "D2: Tool sinh ds-v2-synth", ["task:B0"]),
        ("task:SV1", "SV1: Dịch vụ LitServe deploy model", ["task:MH", "task:ML"]),
        ("task:SV2", "SV2: Dịch vụ FastAPI KIE", ["task:SV1"]),
        ("task:I1", "I1: Đánh giá e2e trên test khóa", ["task:MH", "task:ML"]),
    ]
    for tid, ttitle, tdeps in tasks_data:
        entities.append({
            "id": tid,
            "type": "Task",
            "title": ttitle,
            "body": f"Workflow task {tid}",
            "properties": {"status": "done"},
            "created_at": "2026-10-03 20:00",
        })
        for dep in tdeps:
            edges.append({
                "source": tid,
                "target": dep,
                "type": "depends_on",
                "valid_from": "2026-10-03 20:00",
                "valid_to": None,
                "recorded_at": "2026-10-03 20:00",
                "confidence": 1.0,
            })

    # Model uses dataset
    edges.append({
        "source": "artifact:model:rec-hw-v0.1",
        "target": "artifact:dataset:ds-v1.1",
        "type": "uses",
        "valid_from": "2026-10-04 07:51",
        "valid_to": None,
        "recorded_at": "2026-10-04 07:51",
        "confidence": 1.0,
    })

    # Write entities and edges through the single validated API (overwrite target).
    ensure_kg(target_dir)
    open(entities_path(target_dir), "w", encoding="utf-8").close()
    open(edges_path(target_dir), "w", encoding="utf-8").close()
    for ent in entities:
        upsert_entity(target_dir, ent["id"], ent["type"], ent["title"], ent.get("body", ""),
                      ent.get("properties"), ent.get("created_at"))
    for ed in edges:
        add_edge_checked(target_dir, ed["source"], ed["target"], ed["type"], ed.get("valid_from"),
                         ed.get("valid_to"), ed.get("recorded_at"), ed.get("source_ref"),
                         ed.get("confidence", 1.0), ed.get("properties"))

    print(f"Backfilled Knowledge Graph for '{run_name}' into: {kd}")
    print(f"  - Entities: {len(entities)}")
    print(f"  - Edges:    {len(edges)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sp.add_parser("init", help="Initialize knowledge/ dir in run_dir")
    p_init.add_argument("run_dir")

    # add-entity
    p_ae = sp.add_parser("add-entity", help="Add or update an entity (append-only)")
    p_ae.add_argument("run_dir")
    p_ae.add_argument("--id", required=True, help="Entity ID")
    p_ae.add_argument("--type", required=True, choices=NODE_TYPES)
    p_ae.add_argument("--title", required=True)
    p_ae.add_argument("--body")
    p_ae.add_argument("--props", help="JSON dictionary of extra properties")
    p_ae.add_argument("--created-at")

    # add-edge
    p_ad = sp.add_parser("add-edge", help="Add a typed edge between two entities")
    p_ad.add_argument("run_dir")
    p_ad.add_argument("--source", required=True)
    p_ad.add_argument("--target", required=True)
    p_ad.add_argument("--type", required=True, choices=EDGE_TYPES)
    p_ad.add_argument("--valid-from")
    p_ad.add_argument("--valid-to")
    p_ad.add_argument("--recorded-at")
    p_ad.add_argument("--source-ref")
    p_ad.add_argument("--confidence", type=float, default=1.0)
    p_ad.add_argument("--props", help="JSON dictionary of extra edge properties")
    p_ad.add_argument("--allow-dangling", action="store_true",
                      help="accept an edge whose endpoint is unknown/ill-typed (ordered backfills only; prints a warning)")

    # validate
    p_val = sp.add_parser("validate", help="Validate enum, endpoint types, temporal order")
    p_val.add_argument("run_dir")

    # report (read-only, never modifies files; dùng để dọn dữ liệu writer cũ)
    p_rep = sp.add_parser("report", help="Báo cáo (không tự sửa) cạnh/thực thể sai kiểu hoặc lơ lửng")
    p_rep.add_argument("run_dir")

    # neighbors
    p_nb = sp.add_parser("neighbors", help="List neighbors of a node")
    p_nb.add_argument("run_dir")
    p_nb.add_argument("node_id")
    p_nb.add_argument("--direction", choices=("in", "out", "both"), default="both")
    p_nb.add_argument("--edge-type", choices=EDGE_TYPES)
    p_nb.add_argument("--as-of")

    # path
    p_pt = sp.add_parser("path", help="Find path between two nodes")
    p_pt.add_argument("run_dir")
    p_pt.add_argument("src_id")
    p_pt.add_argument("dst_id")
    p_pt.add_argument("--max-hops", type=int, default=5)
    p_pt.add_argument("--edge-type", choices=EDGE_TYPES)
    p_pt.add_argument("--as-of")

    # explain
    p_exp = sp.add_parser("explain", help="Synthesize full context and history of a node")
    p_exp.add_argument("run_dir")
    p_exp.add_argument("node_id")
    p_exp.add_argument("--as-of")

    # timeline
    p_tl = sp.add_parser("timeline", help="Chronological view of entities and edges")
    p_tl.add_argument("run_dir")
    p_tl.add_argument("--entity-type", choices=NODE_TYPES)
    p_tl.add_argument("--as-of")

    # backfill
    p_bf = sp.add_parser("backfill", help="Backfill knowledge graph from a run's journal/decisions")
    p_bf.add_argument("source_dir")
    p_bf.add_argument("--out-dir")

    a = ap.parse_args()
    cmds = {
        "init": cmd_init,
        "add-entity": cmd_add_entity,
        "add-edge": cmd_add_edge,
        "validate": cmd_validate,
        "report": cmd_report,
        "neighbors": cmd_neighbors,
        "path": cmd_path,
        "explain": cmd_explain,
        "timeline": cmd_timeline,
        "backfill": cmd_backfill,
    }
    cmds[a.cmd](a)


if __name__ == "__main__":
    main()
