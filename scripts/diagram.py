#!/usr/bin/env python3
"""Ve so do Excalidraw cho report pipeline / kien truc mo hinh (stdlib only).

Subcommands:
  render    <spec.json> --out <dir> [--formats excalidraw,svg,obsidian] [--name N]
  validate  <file.excalidraw[.md]>
  from-plan <plan.json> --out <dir> [--formats ...] [--name N]
  from-model <model_spec.json> --out <dir> [--formats ...] [--name N]

Spec don gian:
  {"title": "...", "direction": "LR|TB", "lanes": ["phase1", ...],
   "nodes": [{"id": "...", "label": "...", "kind": "...", "lane": "...", "note": "..."}],
   "edges": [{"from": "...", "to": "...", "label": "...", "style": "solid|dashed"}],
   "legend": "..."}

Quy tac cung (viet lai tu tai lieu EXCALIDRAW_SKILL_GUIDE.md bang loi cua ta):
  - KHONG dung diamond: diem quyet dinh/cong (gate, decision, human-gate) la
    hinh chu nhat bo goc vien net dut (strokeWidth 2).
  - Nhan bat buoc 2 element: shape giu boundElements -> text, text giu
    containerId -> shape (rang buoc hai chieu).
  - Mui ten vuong goc: elbowed=true, roughness=0, roundness=null; diem dau/cuoi
    nam DUNG tren mep hop tai diem giua canh (top/bottom/left/right), dung sai 1px.
  - Khong qua ~25 node moi so do: vuot thi tu tach thanh _p1, _p2, ...

Khong mang, khong cai goi ngoai. UTF-8 KHONG BOM, newline LF.
"""
import argparse
import copy
import html
import json
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

MAX_NODES = 25
TOL = 1.0  # dung sai mui ten tren mep hop (px)

# Bang mau semantic: (nen, vien). 6 mau goc + anh xa mo rong cho pipeline AI.
PALETTE = {
    "api": ("#e7f5ff", "#1971c2"),       # API/entrypoint
    "service": ("#ebfbee", "#2f9e44"),   # service/logic
    "db": ("#fff9db", "#f08c00"),        # DB/storage
    "queue": ("#f3f0ff", "#7950f2"),     # queue/cache/async
    "ai": ("#fff0f6", "#d6336c"),        # AI model/inference
    "gate": ("#ffe3e3", "#e03131"),      # security/auth/gate quyet dinh
    "data": ("#e6fcf5", "#0c8599"),      # data/ETL
    "training": ("#fff0f6", "#d6336c"),  # training job (cung nhom AI)
    "evaluation": ("#e7f5ff", "#1971c2"),  # evaluation/metric (cung nhom entrypoint)
    "artifact": ("#fff9db", "#f08c00"),  # artifact/report (cung nhom storage)
    "external": ("#f1f3f5", "#868e96"),  # du lieu ngoai
    "default": ("#ffffff", "#343a40"),   # du phong
}
# Ten loai hop le trong spec (gồm alias).
KIND_ALIAS = {
    "entrypoint": "api",
    "logic": "service",
    "storage": "db",
    "cache": "queue", "async": "queue",
    "model": "ai", "inference": "ai",
    "backbone": "ai", "neck": "ai", "head": "ai",
    "loss": "evaluation", "metric": "evaluation",
    "etl": "data",
    "report": "artifact",
    "decision": "gate", "human-gate": "gate", "security": "gate", "auth": "gate",
    "input": "data", "output": "artifact",
}
GATE_KINDS = {"gate", "decision", "human-gate", "security", "auth"}
ALLOWED_BG = {bg for bg, _ in PALETTE.values()} | {"transparent"}
ALLOWED_STROKE = {fg for _, fg in PALETTE.values()} | {"#1e1e1e", "#343a40"}

NODE_W, NODE_H = 220, 92
H_GAP, V_GAP, PAD = 70, 50, 40


def norm_kind(kind):
    """Chuan hoa kind (alias -> kind chuan); kind la thi bao loi."""
    if not isinstance(kind, str):
        raise ValueError("kind phai la chuoi, nhan duoc: %r" % (kind,))
    k = kind.strip().lower().replace("_", "-")
    k = KIND_ALIAS.get(k, k)
    if k not in PALETTE:
        raise ValueError("kind la '%s' (hop le: %s)" % (kind, sorted(PALETTE)))
    return k


def is_gate_kind(kind):
    return norm_kind(kind) in ("gate",) or kind.strip().lower().replace("_", "-") in GATE_KINDS


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_text(path, content):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def topo_layers(nodes, edges):
    """Xep tang theo duong dai nhat tu nguon (Kahn + longest path)."""
    ids = [n["id"] for n in nodes]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError("trung id node: %s" % ", ".join(dup))
    pred = {i: [] for i in ids}
    succ = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for e in edges:
        a, b = e["from"], e["to"]
        if a not in pred or b not in pred:
            raise ValueError("canh noi node khong ton tai: %s -> %s" % (a, b))
        if b not in succ[a]:
            succ[a].append(b)
            pred[b].append(a)
            indeg[b] += 1
    queue = sorted([i for i in ids if indeg[i] == 0])
    order, layer = [], {i: 0 for i in ids}
    while queue:
        u = queue.pop(0)
        order.append(u)
        for v in sorted(succ[u]):
            if layer[v] < layer[u] + 1:
                layer[v] = layer[u] + 1
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
        queue.sort()
    if len(order) != len(ids):
        cyc = sorted(set(ids) - set(order))
        raise ValueError("do thi co chu trinh qua: %s" % ", ".join(cyc))
    return order, layer


def layout(spec):
    """Tra ve {node_id: (x, y)} + (W, H)."""
    nodes, edges = spec["nodes"], spec.get("edges", [])
    direction = (spec.get("direction") or "LR").upper()
    if direction not in ("LR", "TB"):
        raise ValueError("direction phai la LR|TB, nhan: %r" % (spec.get("direction"),))
    lanes = spec.get("lanes") or []
    lane_idx = {name: i for i, name in enumerate(lanes)}
    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        norm_kind(n.get("kind", "default"))
        if n.get("lane") is not None and lanes and n["lane"] not in lane_idx:
            raise ValueError("lane la '%s' (lanes: %s)" % (n["lane"], lanes))
    _order, layer = topo_layers(nodes, edges)
    levels = {}
    for nid, lv in layer.items():
        levels.setdefault(lv, []).append(nid)
    pos = {}
    for lv in sorted(levels):
        members = sorted(levels[lv],
                         key=lambda i: (lane_idx.get(by_id[i].get("lane"), 0),
                                        by_id[i].get("lane") or "", i))
        for j, nid in enumerate(members):
            if direction == "LR":
                pos[nid] = (PAD + lv * (NODE_W + H_GAP), PAD + j * (NODE_H + V_GAP))
            else:
                pos[nid] = (PAD + j * (NODE_W + H_GAP), PAD + lv * (NODE_H + V_GAP))
    n_layers = max(layer.values()) + 1 if layer else 1
    widest = max((len(levels[lv]) for lv in levels), default=1)
    if direction == "LR":
        W = PAD * 2 + n_layers * NODE_W + (n_layers - 1) * H_GAP
        H = PAD * 2 + widest * NODE_H + (widest - 1) * V_GAP
    else:
        W = PAD * 2 + widest * NODE_W + (widest - 1) * H_GAP
        H = PAD * 2 + n_layers * NODE_H + (n_layers - 1) * V_GAP
    return pos, W, H


def edge_points(pos, a, b):
    """Diem dau/cuoi tuyet doi: diem giua canh hop gan huong noi nhat."""
    ax, ay = pos[a]
    bx, by = pos[b]
    acx, acy = ax + NODE_W / 2, ay + NODE_H / 2
    bcx, bcy = bx + NODE_W / 2, by + NODE_H / 2
    dx, dy = bcx - acx, bcy - acy
    if abs(dx) >= abs(dy):
        if dx >= 0:
            return (ax + NODE_W, acy), (bx, bcy)
        return (ax, acy), (bx + NODE_W, bcy)
    if dy >= 0:
        return (acx, ay + NODE_H), (bcx, by)
    return (acx, ay), (bcx, by + NODE_H)


def edge_midpoints(x, y):
    return [(x + NODE_W / 2, y), (x + NODE_W / 2, y + NODE_H),
            (x, y + NODE_H / 2), (x + NODE_W, y + NODE_H / 2)]


# ---------------------------------------------------------------- excalidraw elements
_uid = [0]


def _new_id(prefix):
    _uid[0] += 1
    return "%s%d" % (prefix, _uid[0])


def shape_element(nid, n, x, y):
    kind = norm_kind(n.get("kind", "default"))
    bg, fg = PALETTE[kind]
    gate = is_gate_kind(n.get("kind", "default"))
    text_id = "t_%s" % nid
    shape = {
        "id": nid, "type": "rectangle", "x": x, "y": y,
        "width": NODE_W, "height": NODE_H, "angle": 0,
        "strokeColor": fg, "backgroundColor": bg, "fillStyle": "solid",
        "strokeWidth": 2 if gate else 1,
        "strokeStyle": "dashed" if gate else "solid",
        "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": {"type": 3}, "boundElements": [{"type": "text", "id": text_id}],
        "link": None, "locked": False,
    }
    return shape, text_id


def text_element(tid, shape_id, x, y, label, note=None):
    full = label if not note else "%s\n%s" % (label, note)
    lines = full.split("\n")
    longest = max((len(s) for s in lines), default=1)
    tw = min(NODE_W - 16, max(40, longest * 8 + 8))
    th = max(20, len(lines) * 20)
    return {
        "id": tid, "type": "text",
        "x": round(x + (NODE_W - tw) / 2, 1), "y": round(y + (NODE_H - th) / 2, 1),
        "width": tw, "height": th, "angle": 0,
        "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
        "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "boundElements": [],
        "link": None, "locked": False,
        "text": full, "fontSize": 16, "fontFamily": 1,
        "textAlign": "center", "verticalAlign": "middle",
        "containerId": shape_id, "originalText": full, "lineHeight": 1.25,
    }


def arrow_element(aid, a, b, p1, p2, label=None, dashed=False):
    x1, y1 = p1
    x2, y2 = p2
    elems = [{
        "id": aid, "type": "arrow", "x": x1, "y": y1,
        "width": abs(x2 - x1), "height": abs(y2 - y1), "angle": 0,
        "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 1,
        "strokeStyle": "dashed" if dashed else "solid",
        "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "boundElements": [],
        "link": None, "locked": False,
        "points": [[0, 0], [round(x2 - x1, 1), round(y2 - y1, 1)]],
        "lastCommittedPoint": None,
        "startBinding": {"elementId": a, "focus": 0, "gap": 1},
        "endBinding": {"elementId": b, "focus": 0, "gap": 1},
        "startArrowhead": None, "endArrowhead": "arrow",
        "elbowed": True,
    }]
    if label:
        tid = "t_%s" % aid
        elems[0]["boundElements"] = [{"type": "text", "id": tid}]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        elems.append({
            "id": tid, "type": "text", "x": round(mx - 40, 1), "y": round(my - 12, 1),
            "width": 80, "height": 24, "angle": 0,
            "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
            "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": None, "boundElements": [],
            "link": None, "locked": False,
            "text": label, "fontSize": 14, "fontFamily": 1,
            "textAlign": "center", "verticalAlign": "middle",
            "containerId": aid, "originalText": label, "lineHeight": 1.25,
        })
    return elems


def build_excalidraw(spec, pos):
    _uid[0] = 0
    by_id = {n["id"]: n for n in spec["nodes"]}
    elements = []
    for nid, n in by_id.items():
        x, y = pos[nid]
        shape, tid = shape_element(nid, n, x, y)
        elements.append(shape)
        elements.append(text_element(tid, nid, x, y, n.get("label", nid), n.get("note")))
    for i, e in enumerate(spec.get("edges", [])):
        p1, p2 = edge_points(pos, e["from"], e["to"])
        elements.extend(arrow_element("e%d_%s_%s" % (i, e["from"], e["to"]),
                                      e["from"], e["to"], p1, p2,
                                      e.get("label"), (e.get("style") or "solid") == "dashed"))
    return {"type": "excalidraw", "version": 2, "source": "ai-pipeline-diagram",
            "elements": elements,
            "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
            "files": {}}


# ---------------------------------------------------------------- SVG
def wrap_label(text, per_line):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > per_line and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def build_svg(spec, pos, W, H):
    E = html.escape
    by_id = {n["id"]: n for n in spec["nodes"]}
    parts = ['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" role="img">'
             % (W, H),
             '<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="7" '
             'refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#1e1e1e"/>'
             "</marker></defs>",
             '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (W, H),
             "<title>%s</title>" % E(spec.get("title", ""))]
    for i, e in enumerate(spec.get("edges", [])):
        (x1, y1), (x2, y2) = edge_points(pos, e["from"], e["to"])
        dash = ' stroke-dasharray="6 4"' if (e.get("style") or "solid") == "dashed" else ""
        if x1 == x2 or y1 == y2:
            parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#1e1e1e" '
                         'stroke-width="1.6" marker-end="url(#ar)"%s/>'
                         % (x1, y1, x2, y2, dash))
        else:
            mx = x2 if abs(x2 - x1) >= abs(y2 - y1) else x1
            my = y1 if abs(x2 - x1) >= abs(y2 - y1) else y2
            parts.append('<polyline points="%s,%s %s,%s %s,%s" fill="none" '
                         'stroke="#1e1e1e" stroke-width="1.6" marker-end="url(#ar)"%s/>'
                         % (x1, y1, mx, my, x2, y2, dash))
        if e.get("label"):
            parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="12" '
                         'fill="#333" font-family="system-ui,sans-serif">%s</text>'
                         % ((x1 + x2) / 2, (y1 + y2) / 2 - 6, E(e["label"])))
    for nid, n in by_id.items():
        x, y = pos[nid]
        kind = norm_kind(n.get("kind", "default"))
        bg, fg = PALETTE[kind]
        gate = is_gate_kind(n.get("kind", "default"))
        dash = ' stroke-dasharray="8 5"' if gate else ""
        rx = 14 if gate else 8
        sw = 2 if gate else 1.5
        parts.append('<rect x="%s" y="%s" width="%d" height="%d" rx="%d" fill="%s" '
                     'stroke="%s" stroke-width="%s"%s/>'
                     % (x, y, NODE_W, NODE_H, rx, bg, fg, sw, dash))
        label_lines = wrap_label(n.get("label", nid), 22)[:2]
        note_lines = wrap_label(n.get("note", ""), 30)[:1] if n.get("note") else []
        cy = y + 26 + (8 if not note_lines else 0)
        for j, ln in enumerate(label_lines):
            parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="13" '
                         'font-weight="600" fill="#1a1a1a" '
                         'font-family="system-ui,sans-serif">%s</text>'
                         % (x + NODE_W / 2, cy + j * 16, E(ln)))
        for j, ln in enumerate(note_lines):
            parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="11" '
                         'fill="#444" font-family="system-ui,sans-serif">%s</text>'
                         % (x + NODE_W / 2, y + NODE_H - 12 + j * 13, E(ln)))
    if spec.get("legend"):
        parts.append('<text x="%d" y="%d" font-size="11" fill="#666" '
                     'font-family="system-ui,sans-serif">%s</text>'
                     % (PAD, H - 10, E(spec["legend"])))
    return "".join(parts) + "</svg>"


# ---------------------------------------------------------------- Obsidian
def build_obsidian_md(spec, doc):
    """Viet .excalidraw.md cho plugin Obsidian Excalidraw.

    Dinh dang gom khoi YAML, bang '# Text Elements' va khoi '# Drawing' chua
    JSON chua nen (uncompressed). Luu y CHUA XAC MINH: chung toi chua mo file
    that trong Obsidian/Excalidraw (xem skill ai-pipeline-diagram, muc gioi han);
    plugin goc (zsviczian/obsidian-excalidraw-plugin) con chap nhan ca khoi
    ```compressed-json (LZ-string) ma cong cu nay khong sinh ra.
    """
    texts = [(el["id"], el.get("containerId", ""), el.get("text", ""))
             for el in doc["elements"] if el.get("type") == "text"]
    lines = ["---", "excalidraw-plugin: parsed", "---", "",
             "# %s" % spec.get("title", "diagram"), "",
             "# Text Elements", "",
             "| id | container | text |", "|---|---|---|"]
    for tid, cid, t in texts:
        cell = str(t).replace("|", "\\|").replace("\n", "<br>")
        lines.append("| %s | %s | %s |" % (tid, cid, cell))
    lines += ["", "# Drawing", "", "```json",
              json.dumps(doc, ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- validate
def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def extract_doc(data):
    """Nhan ca .excalidraw JSON va .excalidraw.md (khoi ```json)."""
    if isinstance(data, dict) and "elements" in data:
        return data
    if isinstance(data, str):
        import re
        m = re.search(r"```json\s*(.*?)```", data, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except ValueError:
            return None
    return None


def validate_doc(doc):
    """Tra ve danh sach loi (rong = hop le)."""
    errs = []
    if not isinstance(doc, dict) or not isinstance(doc.get("elements"), list):
        return ["tai lieu khong phai excalidraw hop le (thieu 'elements')"]
    els = doc["elements"]
    seen = {}
    for el in els:
        eid = el.get("id")
        if eid in seen:
            errs.append("id trung: '%s'" % eid)
        else:
            seen[eid] = el
        if el.get("type") == "diamond":
            errs.append("cam diamond: '%s' (dung hinh chu nhat bo goc net dut)" % eid)
        for k in ("x", "y", "width", "height"):
            if not _finite(el.get(k)):
                errs.append("toa do khong huu han: '%s'.%s=%r" % (eid, k, el.get(k)))
        if _finite(el.get("width")) and el["width"] <= 0 and el.get("type") != "arrow":
            errs.append("width <= 0: '%s'" % eid)
        if _finite(el.get("height")) and el["height"] <= 0 and el.get("type") != "arrow":
            errs.append("height <= 0: '%s'" % eid)
        if el.get("type") == "arrow":
            pts = el.get("points")
            if not (isinstance(pts, list) and len(pts) >= 2
                    and all(isinstance(p, list) and len(p) == 2
                            and _finite(p[0]) and _finite(p[1]) for p in pts)):
                errs.append("mui ten '%s' thieu points huu han" % eid)
            if el.get("elbowed") is not True:
                errs.append("mui ten '%s' phai elbowed:true" % eid)
            if el.get("roughness") != 0:
                errs.append("mui ten '%s' phai roughness:0" % eid)
            if el.get("roundness") is not None:
                errs.append("mui ten '%s' phai roundness:null" % eid)
        bg = el.get("backgroundColor")
        if bg is not None and bg not in ALLOWED_BG:
            errs.append("palette la (backgroundColor): '%s'='%s'" % (eid, bg))
        fg = el.get("strokeColor")
        if fg is not None and fg not in ALLOWED_STROKE:
            errs.append("palette la (strokeColor): '%s'='%s'" % (eid, fg))
    # Rang buoc nhan 2 chieu.
    for el in els:
        if el.get("type") == "text":
            cid = el.get("containerId")
            if not cid:
                errs.append("text '%s' thieu containerId" % el.get("id"))
            elif cid not in seen:
                errs.append("text '%s' tro container khong ton tai '%s'" % (el.get("id"), cid))
            else:
                bound = [b.get("id") for b in (seen[cid].get("boundElements") or [])
                         if b.get("type") == "text"]
                if el.get("id") not in bound:
                    errs.append("thieu binding hai chieu: '%s'.boundElements khong giu text '%s'"
                                % (cid, el.get("id")))
    for el in els:
        for b in (el.get("boundElements") or []):
            if b.get("type") == "text":
                t = seen.get(b.get("id"))
                if t is None:
                    errs.append("boundElements tro text khong ton tai: '%s'->'%s'"
                                % (el.get("id"), b.get("id")))
                elif t.get("containerId") != el.get("id"):
                    errs.append("thieu binding hai chieu: text '%s'.containerId != '%s'"
                                % (b.get("id"), el.get("id")))
    # Mui ten: diem dau/cuoi tren mep hop (trung diem canh, dung sai 1px).
    rects = {el["id"]: el for el in els if el.get("type") == "rectangle"}
    for el in els:
        if el.get("type") != "arrow":
            continue
        pts = el.get("points")
        if not (isinstance(pts, list) and len(pts) >= 2):
            continue
        sb = (el.get("startBinding") or {}).get("elementId")
        eb = (el.get("endBinding") or {}).get("elementId")
        for which, idx, ref in (("dau", 0, sb), ("cuoi", -1, eb)):
            if not ref or ref not in rects:
                # Nhan tren mui ten (text) hoac ref la: bo qua mep, da kiem o tren.
                if ref is None or (ref in seen and seen[ref].get("type") == "arrow"):
                    continue
                errs.append("mui ten '%s' %s tro shape la '%s'" % (el.get("id"), which, ref))
                continue
            r = rects[ref]
            px = el["x"] + pts[idx][0]
            py = el["y"] + pts[idx][1]
            mids = edge_midpoints(r["x"], r["y"])
            if not all(_finite(v) for v in (px, py, r["x"], r["y"])):
                errs.append("mui ten '%s' %s toa do khong huu han" % (el.get("id"), which))
                continue
            if min(math.hypot(px - mx, py - my) for mx, my in mids) > TOL + 1e-9:
                errs.append("mui ten '%s' %s lech mep hop '%s' (diem [%.1f, %.1f])"
                            % (el.get("id"), which, ref, px, py))
    return errs


def validate_file(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = raw
    doc = extract_doc(data)
    if doc is None:
        return ["khong doc duoc excalidraw tu '%s'" % path]
    return validate_doc(doc)


# ---------------------------------------------------------------- render
def spec_from_json(data):
    if not isinstance(data, dict):
        raise ValueError("spec phai la object JSON")
    for n in data.get("nodes", []):
        if "id" not in n or "label" not in n:
            raise ValueError("node thieu id/label: %r" % (n,))
        n["kind"] = norm_kind(n.get("kind", "default"))
    for e in data.get("edges", []):
        if "from" not in e or "to" not in e:
            raise ValueError("canh thieu from/to: %r" % (e,))
        e["style"] = e.get("style") or "solid"
        if e["style"] not in ("solid", "dashed"):
            raise ValueError("style canh phai solid|dashed: %r" % (e,))
    return data


def split_spec(spec):
    """Tach spec >25 node thanh cac spec con (giua lai canh noi bo)."""
    nodes = spec["nodes"]
    if len(nodes) <= MAX_NODES:
        return [spec]
    order, _layer = topo_layers(nodes, spec.get("edges", []))
    rank = {nid: i for i, nid in enumerate(order)}
    ordered = sorted(nodes, key=lambda n: rank[n["id"]])
    parts, dropped = [], []
    for i in range(0, len(ordered), MAX_NODES):
        chunk = ordered[i:i + MAX_NODES]
        keep = {n["id"] for n in chunk}
        sub = copy.deepcopy(spec)
        sub["nodes"] = chunk
        sub["edges"] = [e for e in spec.get("edges", [])
                        if e["from"] in keep and e["to"] in keep]
        dropped += [(e["from"], e["to"]) for e in spec.get("edges", [])
                    if not (e["from"] in keep and e["to"] in keep)]
        sub["title"] = "%s (phan %d/%d)" % (spec.get("title", "diagram"),
                                            len(parts) + 1,
                                            (len(ordered) - 1) // MAX_NODES + 1)
        parts.append(sub)
    if dropped:
        print("canh: tach %d node thanh %d so do con, bo %d canh lien phan: %s"
              % (len(nodes), len(parts), len(dropped), dropped[:10]))
        link_note = "Noi sang phan khac: %s" % ", ".join("%s->%s" % (a, b) for a, b in dropped)
        for sub in parts:
            sub["legend"] = ((sub.get("legend") or "") + " | " + link_note).strip(" |")
    return parts


def render_spec(spec, out_dir, formats, name):
    spec = spec_from_json(copy.deepcopy(spec))
    os.makedirs(out_dir, exist_ok=True)
    written = []
    parts = split_spec(spec)
    for i, part in enumerate(parts):
        suffix = "" if len(parts) == 1 else "_p%d" % (i + 1)
        base = name + suffix
        pos, W, H = layout(part)
        doc = build_excalidraw(part, pos)
        if "excalidraw" in formats:
            p = os.path.join(out_dir, base + ".excalidraw")
            write_text(p, json.dumps(doc, ensure_ascii=False, indent=1))
            written.append(p)
        if "svg" in formats:
            p = os.path.join(out_dir, base + ".svg")
            write_text(p, build_svg(part, pos, W, H))
            written.append(p)
        if "obsidian" in formats:
            p = os.path.join(out_dir, base + ".excalidraw.md")
            write_text(p, build_obsidian_md(part, doc))
            written.append(p)
    for p in written:
        print("wrote", p)
    return written


# ---------------------------------------------------------------- from-plan / from-model
def _registry_groups():
    here = os.path.dirname(os.path.abspath(__file__))
    reg = os.path.join(os.path.dirname(here), "roles", "registry.json")
    try:
        data = json.load(open(reg, encoding="utf-8"))
        groups = data.get("groups", {})
        inv = {}
        for g, members in groups.items():
            for m in members:
                inv[m] = g
        return inv
    except (OSError, ValueError):
        return {}


GROUP_KIND = {"code": "service", "debate": "api", "analysis": "data"}


def plan_to_spec(plan):
    inv = _registry_groups()
    tasks = plan.get("tasks", [])
    lanes, seen_lane = [], set()
    for t in tasks:
        lane = "gate" if t.get("kind") == "gate" else (t.get("phase") or "build")
        if lane not in seen_lane:
            lanes.append(lane)
            seen_lane.add(lane)
    nodes, edges = [], []
    for t in tasks:
        if t.get("kind") == "gate":
            kind, lane = "human-gate", "gate"
            note = "cong kiem soat"
        else:
            kind = GROUP_KIND.get(inv.get(t.get("role", ""), ""), "service")
            lane = t.get("phase") or "build"
            note = " · ".join(x for x in (t.get("role"), t.get("agent")) if x)
        nodes.append({"id": t["id"], "label": t.get("title", t["id"]),
                      "kind": kind, "lane": lane, "note": note or None})
        for d in t.get("deps", []):
            edges.append({"from": d, "to": t["id"]})
    return {"title": plan.get("title", plan.get("run_id", "plan")),
            "direction": "LR", "lanes": lanes, "nodes": nodes, "edges": edges,
            "legend": "Gate/cong = chu nhat bo goc net dut (khong diamond)"}


PART_KIND = {"input": "data", "backbone": "ai", "neck": "ai", "head": "ai",
             "loss": "evaluation", "postprocess": "service", "output": "artifact",
             "data": "data", "evaluation": "evaluation", "artifact": "artifact",
             "service": "service", "gate": "gate"}


def model_to_spec(ms):
    blocks = ms.get("blocks", [])
    nodes = []
    for b in blocks:
        part = (b.get("part") or "backbone").strip().lower()
        kind = PART_KIND.get(part, "ai")
        label = b.get("label", b["id"])
        if b.get("repeat") and int(b["repeat"]) > 1:
            label = "%s x%d" % (label, int(b["repeat"]))
        nodes.append({"id": b["id"], "label": label, "kind": kind,
                      "lane": part, "note": b.get("tensor")})
    edges = [{"from": e["from"], "to": e["to"], "label": e.get("label"),
              "style": e.get("style", "solid")}
             for e in ms.get("edges", [])]
    lanes = []
    for b in blocks:
        p = (b.get("part") or "backbone").strip().lower()
        if p not in lanes:
            lanes.append(p)
    title = ms.get("model", "model")
    if ms.get("version"):
        title += " %s" % ms["version"]
    return {"title": title, "direction": ms.get("direction", "LR"),
            "lanes": lanes, "nodes": nodes, "edges": edges,
            "legend": "Khoi lap ghi xN; kich thuoc tensor o nhan phu"}


def parse_formats(s):
    fmts = [x.strip().lower() for x in s.split(",")]
    for f in fmts:
        if f not in ("excalidraw", "svg", "obsidian"):
            raise ValueError("formats la: excalidraw,svg,obsidian (nhan '%s')" % f)
    return fmts


def main():
    ap = argparse.ArgumentParser(prog="diagram.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("render", help="spec JSON -> .excalidraw + .svg + .excalidraw.md")
    p.add_argument("spec")
    p.add_argument("--out", required=True)
    p.add_argument("--formats", default="excalidraw,svg,obsidian")
    p.add_argument("--name", default="diagram")
    p = sub.add_parser("validate", help="kiem tra .excalidraw hop le")
    p.add_argument("file")
    p = sub.add_parser("from-plan", help="plan.json (DAG task) -> so do pipeline")
    p.add_argument("plan")
    p.add_argument("--out", required=True)
    p.add_argument("--formats", default="excalidraw,svg,obsidian")
    p.add_argument("--name", default="plan")
    p = sub.add_parser("from-model", help="model_spec.json -> so do kien truc mo hinh")
    p.add_argument("model_spec")
    p.add_argument("--out", required=True)
    p.add_argument("--formats", default="excalidraw,svg,obsidian")
    p.add_argument("--name", default="model")
    a = ap.parse_args()
    try:
        if a.cmd == "render":
            render_spec(load_json(a.spec), a.out, parse_formats(a.formats), a.name)
        elif a.cmd == "validate":
            errs = validate_file(a.file)
            if errs:
                print("INVALID %s (%d loi):" % (a.file, len(errs)))
                for e in errs:
                    print(" -", e)
                return 1
            print("VALID", a.file)
        elif a.cmd == "from-plan":
            render_spec(plan_to_spec(load_json(a.plan)), a.out,
                        parse_formats(a.formats), a.name)
        elif a.cmd == "from-model":
            render_spec(model_to_spec(load_json(a.model_spec)), a.out,
                        parse_formats(a.formats), a.name)
    except (ValueError, OSError) as exc:
        print("loi:", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
