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
  - Khong qua ~25 node moi so do: vuot thi tu tach thanh _p1, _p2, ... + tong quan.
  - Dinh tuyen kenh (v0.2): moi doan mui ten di trong KENH (khe giua cac cot/hang),
    khong doan nao cat noi that hop cua node khong phai dau/cuoi; canh cung dich
    gop chung duong bus vao voi diem re nhanh; viewBox khit noi dung + le deu.

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

DIAGRAM_VERSION = "diagram-v0.2-dg2"

MAX_NODES = 25
TOL = 1.0  # dung sai mui ten tren mep hop (px)
TEXT_TOL = 2.0  # dung sai chu trong hop (px, do lam tron)

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
NODE_MAX_W = 340  # hop chi gian toi da toi day, sau do xuong dong them
H_GAP, V_GAP, PAD = 70, 50, 40
VIEW_MARGIN = 24  # le deu quanh viewBox khit
LABEL_FS, NOTE_FS, EDGE_FS = 14, 11, 12  # chu nhan doc duoc (>=12 o kich co mac dinh)
LANE_FS, LEGEND_FS = 13, 11
LABEL_LH, NOTE_LH = 18, 15  # chieu cao dong nhan chinh / nhan phu
TEXT_PAD_X, TEXT_PAD_Y = 12, 12  # dem chu trong hop
ARROW_LABEL_NEAR = 100.0  # nhan canh phai gan duong mui ten (px)

# Mau bang phase/lane (xoay vong theo danh sach PALETTE).
LANE_ORDER = ["data", "api", "queue", "artifact", "service", "ai", "evaluation", "external"]


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


# ---------------------------------------------------------------- do rong chu (ca tieng Viet co dau va CJK)
def _is_wide_char(ch):
    """Ky tu CJK/allwidth chiem ~1em; chu Latin (ke ca co dau Viet) ~0.58em."""
    o = ord(ch)
    return (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0x9FFF
            or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF
            or 0xFE30 <= o <= 0xFE4F or 0xFF00 <= o <= 0xFFEF
            or 0x20000 <= o <= 0x3FFFF)


def text_width(s, fs):
    """Uoc luong chieu rong chu (px) theo co chu fs."""
    w = 0.0
    for ch in str(s):
        w += float(fs) if _is_wide_char(ch) else float(fs) * 0.58
    return w


def wrap_text(text, fs, max_w):
    """Xuong dong theo tu (tu dai cat theo ky tu); khong bao gio cat bo chu."""
    words, lines, cur = str(text).split(), [], ""
    cur_w = 0.0
    space_w = text_width(" ", fs)
    for w in words:
        ww = text_width(w, fs)
        if ww > max_w:
            if cur:
                lines.append(cur)
                cur, cur_w = "", 0.0
            part = ""
            part_w = 0.0
            for ch in w:
                cw = text_width(ch, fs)
                if part and part_w + cw > max_w:
                    lines.append(part)
                    part, part_w = "", 0.0
                part += ch
                part_w += cw
            cur, cur_w = part, part_w
            continue
        add = (space_w + ww) if cur else ww
        if cur and cur_w + add > max_w:
            lines.append(cur)
            cur, cur_w = w, ww
        else:
            cur = (cur + " " + w).strip()
            cur_w += add
    if cur:
        lines.append(cur)
    return lines or [""]


def node_text_lines(node):
    """Toan bo dong nhan (chinh + phu); khong cat bo dong nao."""
    inner = NODE_MAX_W - TEXT_PAD_X * 2
    label_lines = wrap_text(node.get("label", node["id"]), LABEL_FS, inner)
    note = node.get("note")
    note_lines = wrap_text(note, NOTE_FS, inner) if note else []
    return label_lines, note_lines


def node_size(node):
    """Kich thuoc hop tu dong theo do dai nhan; nhan ngan giu kich co mac dinh."""
    label_lines, note_lines = node_text_lines(node)
    need = NODE_W - TEXT_PAD_X * 2
    for ln in label_lines:
        need = max(need, text_width(ln, LABEL_FS))
    for ln in note_lines:
        need = max(need, text_width(ln, NOTE_FS))
    need += TEXT_PAD_X * 2
    if need <= NODE_W:
        w = float(NODE_W)
    else:
        w = float(min(NODE_MAX_W, math.ceil(need)))
        label_lines, note_lines = (
            wrap_text(node.get("label", node["id"]), LABEL_FS, w - TEXT_PAD_X * 2),
            wrap_text(node.get("note"), NOTE_FS, w - TEXT_PAD_X * 2) if node.get("note") else [],
        )
    h = (TEXT_PAD_Y * 2 + len(label_lines) * LABEL_LH
         + (6 + len(note_lines) * NOTE_LH if note_lines else 0))
    return w, float(max(NODE_H, math.ceil(h)))


def lane_of(node, default="build"):
    return node.get("lane") or default


def lane_color(lane, lanes):
    """Mau on dinh cho tung lane theo thu tu khai bao."""
    order = list(lanes) if lanes else []
    if lane not in order:
        order = order + [lane]
    return PALETTE[LANE_ORDER[order.index(lane) % len(LANE_ORDER)]]


# ---------------------------------------------------------------- layout (can giua tang)
def layout_full(spec):
    """Bo cuc + kich thuoc tung hop; tra ve (pos, sizes, W, H, aux)."""
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
    sizes = {n["id"]: node_size(n) for n in nodes}
    levels = {}
    for nid, lv in layer.items():
        levels.setdefault(lv, []).append(nid)
    for lv in levels:
        levels[lv].sort(key=lambda i: (lane_idx.get(by_id[i].get("lane"), 0),
                                       by_id[i].get("lane") or "", i))
    n_layers = max(layer.values()) + 1 if layer else 1
    aux = {"direction": direction, "layer": layer, "sizes": sizes, "levels": levels}
    pos = {}
    if direction == "LR":
        col_w = {lv: max(sizes[n][0] for n in members) for lv, members in levels.items()}
        col_x, x = {}, float(PAD)
        for lv in sorted(levels):
            col_x[lv] = x
            x += col_w[lv] + H_GAP
        stacks = {lv: sum(sizes[n][1] for n in levels[lv]) + V_GAP * (len(levels[lv]) - 1)
                  for lv in levels}
        tallest = max(stacks.values()) if stacks else 0
        for lv in sorted(levels):
            y = PAD + (tallest - stacks[lv]) / 2.0
            for nid in levels[lv]:
                pos[nid] = (round(col_x[lv], 1), round(y, 1))
                y += sizes[nid][1] + V_GAP
        W = PAD * 2 + sum(col_w.values()) + H_GAP * (n_layers - 1)
        H = PAD * 2 + tallest
        aux.update({"col_x": col_x, "col_w": col_w})
    else:
        row_h = {lv: max(sizes[n][1] for n in members) for lv, members in levels.items()}
        row_y, y = {}, float(PAD)
        for lv in sorted(levels):
            row_y[lv] = y
            y += row_h[lv] + V_GAP
        widths = {lv: sum(sizes[n][0] for n in levels[lv]) + H_GAP * (len(levels[lv]) - 1)
                  for lv in levels}
        widest = max(widths.values()) if widths else 0
        for lv in sorted(levels):
            x = PAD + (widest - widths[lv]) / 2.0
            for nid in levels[lv]:
                pos[nid] = (round(x, 1), round(row_y[lv], 1))
                x += sizes[nid][0] + H_GAP
        W = PAD * 2 + widest
        H = PAD * 2 + sum(row_h.values()) + V_GAP * (n_layers - 1)
        aux.update({"row_y": row_y, "row_h": row_h})
    return pos, sizes, round(W, 1), round(H, 1), aux


def layout(spec):
    """Tra ve {node_id: (x, y)} + (W, H)."""
    pos, _sizes, W, H, _aux = layout_full(spec)
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


def edge_midpoints(x, y, w=NODE_W, h=NODE_H):
    return [(x + w / 2, y), (x + w / 2, y + h),
            (x, y + h / 2), (x + w, y + h / 2)]


# ---------------------------------------------------------------- dinh tuyen kenh (khong xuyen hop la)
def _port(pos, sizes, nid, side):
    x, y = pos[nid]
    w, h = sizes[nid]
    if side == "right":
        return (x + w, y + h / 2.0)
    if side == "left":
        return (x, y + h / 2.0)
    if side == "top":
        return (x + w / 2.0, y)
    return (x + w / 2.0, y + h)


def _seg_hits_box(x1, y1, x2, y2, rx, ry, rw, rh):
    """Doan truc giao co cat noi that hop khong (tiep bien thi khong)."""
    eps = 1e-9
    if abs(x1 - x2) < eps:  # doc
        if not (rx + eps < x1 < rx + rw - eps):
            return False
        lo, hi = (y1, y2) if y1 < y2 else (y2, y1)
        return min(hi, ry + rh) - max(lo, ry) > eps
    if abs(y1 - y2) < eps:  # ngang
        if not (ry + eps < y1 < ry + rh - eps):
            return False
        lo, hi = (x1, x2) if x1 < x2 else (x2, x1)
        return min(hi, rx + rw) - max(lo, rx) > eps
    return True  # khong bao gio xay ra voi duong kenh; phong thu


def _track_free(yt_or_xt, c0, c1, boxes, fixed):
    """Kiem tra duong ngang (fixed='y') hay doc (fixed='x') co sach khong."""
    for (rx, ry, rw, rh) in boxes:
        if fixed == "y":
            if _seg_hits_box(c0, yt_or_xt, c1, yt_or_xt, rx, ry, rw, rh):
                return False
        else:
            if _seg_hits_box(yt_or_xt, c0, yt_or_xt, c1, rx, ry, rw, rh):
                return False
    return True


def _gap_tracks_lr(pos, sizes, aux, la, lb):
    """Cac ung vien duong ngang tu do giua 2 kenh (giua hang + bien)."""
    cands, seen = [], set()
    for lv in range(min(la, lb), max(la, lb) + 1):
        members = aux["levels"].get(lv, [])
        ys = sorted(pos[n][1] for n in members)
        for i in range(len(ys) - 1):
            a_box = max(pos[n][1] + sizes[n][1] for n in members
                        if abs(pos[n][1] - ys[i]) < 1e-9)
            b_box = min(pos[n][1] for n in members
                        if abs(pos[n][1] - ys[i + 1]) < 1e-9)
            mid = (a_box + b_box) / 2.0
            if mid not in seen:
                seen.add(mid)
                cands.append(mid)
    return cands


def _route_lr(a, b, pos, sizes, aux):
    layer = aux["layer"]
    la, lb = layer[a], layer[b]
    col_x, col_w = aux["col_x"], aux["col_w"]
    boxes = [(pos[n][0], pos[n][1], sizes[n][0], sizes[n][1]) for n in pos]
    col_l = lambda lv: col_x[lv]
    col_r = lambda lv: col_x[lv] + col_w[lv]

    def channel_before(lv):
        if lv - 1 in col_x:
            return col_r(lv - 1) + (col_l(lv) - col_r(lv - 1)) / 2.0
        return col_l(lv) - H_GAP / 2.0

    def channel_after(lv):
        if lv + 1 in col_x:
            return col_r(lv) + (col_l(lv + 1) - col_r(lv)) / 2.0
        return col_r(lv) + H_GAP / 2.0

    if la == lb:  # cung tang: vong chu U trong kenh ben phai
        ch = channel_after(la)
        p0 = _port(pos, sizes, a, "right")
        p1 = _port(pos, sizes, b, "right")
        return _dedup([p0, (ch, p0[1]), (ch, p1[1]), p1])
    if la < lb:  # xuoi: ra phai, bus doc o kenh vao cua dich
        p0 = _port(pos, sizes, a, "right")
        p1 = _port(pos, sizes, b, "left")
        exit_cx = channel_after(la)
        bus_x = channel_before(lb)
        if abs(exit_cx - bus_x) < 1e-9:  # tang lien ke: di thang qua 1 kenh
            return _dedup([p0, (bus_x, p0[1]), (bus_x, p1[1]), p1])
        cands = [p0[1], p1[1]] + _gap_tracks_lr(pos, sizes, aux, la, lb)
        tops = min(r[1] for r in boxes)
        cands.append(tops - V_GAP / 2.0)  # duong thoat: tren tat ca (luon sach)
        yt = next((c for c in cands
                   if _track_free(c, min(exit_cx, bus_x), max(exit_cx, bus_x),
                                  [r for n, r in zip(pos, boxes) if n not in (a, b)], "y")),
                  cands[-1])
        return _dedup([p0, (exit_cx, p0[1]), (exit_cx, yt), (bus_x, yt),
                       (bus_x, p1[1]), p1])
    # nguoc (phong thu): ra trai, bus doc o kenh phai cua dich
    p0 = _port(pos, sizes, a, "left")
    p1 = _port(pos, sizes, b, "right")
    exit_cx = channel_before(la)
    bus_x = channel_after(lb)
    if abs(exit_cx - bus_x) < 1e-9:
        return _dedup([p0, (bus_x, p0[1]), (bus_x, p1[1]), p1])
    cands = [p0[1], p1[1]] + _gap_tracks_lr(pos, sizes, aux, lb, la)
    bots = max(r[1] + r[3] for r in boxes)
    cands.append(bots + V_GAP / 2.0)
    yt = next((c for c in cands
               if _track_free(c, min(exit_cx, bus_x), max(exit_cx, bus_x),
                              [r for n, r in zip(pos, boxes) if n not in (a, b)], "y")),
              cands[-1])
    return _dedup([p0, (exit_cx, p0[1]), (exit_cx, yt), (bus_x, yt),
                   (bus_x, p1[1]), p1])


def _route_tb(a, b, pos, sizes, aux):
    layer = aux["layer"]
    la, lb = layer[a], layer[b]
    row_y, row_h = aux["row_y"], aux["row_h"]
    boxes = [(pos[n][0], pos[n][1], sizes[n][0], sizes[n][1]) for n in pos]
    row_t = lambda lv: row_y[lv]
    row_b = lambda lv: row_y[lv] + row_h[lv]

    def channel_before(lv):
        if lv - 1 in row_y:
            return row_b(lv - 1) + (row_t(lv) - row_b(lv - 1)) / 2.0
        return row_t(lv) - V_GAP / 2.0

    def channel_after(lv):
        if lv + 1 in row_y:
            return row_b(lv) + (row_t(lv + 1) - row_b(lv)) / 2.0
        return row_b(lv) + V_GAP / 2.0

    def gap_tracks():
        cands, seen = [], set()
        for lv in range(min(la, lb), max(la, lb) + 1):
            members = aux["levels"].get(lv, [])
            xs = sorted(pos[n][0] for n in members)
            for i in range(len(xs) - 1):
                a_box = max(pos[n][0] + sizes[n][0] for n in members
                            if abs(pos[n][0] - xs[i]) < 1e-9)
                b_box = min(pos[n][0] for n in members
                            if abs(pos[n][0] - xs[i + 1]) < 1e-9)
                mid = (a_box + b_box) / 2.0
                if mid not in seen:
                    seen.add(mid)
                    cands.append(mid)
        return cands

    if la == lb:
        ch = channel_after(la)
        p0 = _port(pos, sizes, a, "bottom")
        p1 = _port(pos, sizes, b, "bottom")
        return _dedup([p0, (p0[0], ch), (p1[0], ch), p1])
    if la < lb:
        p0 = _port(pos, sizes, a, "bottom")
        p1 = _port(pos, sizes, b, "top")
        exit_cy = channel_after(la)
        bus_y = channel_before(lb)
        if abs(exit_cy - bus_y) < 1e-9:
            return _dedup([p0, (p0[0], bus_y), (p1[0], bus_y), p1])
        cands = [p0[0], p1[0]] + gap_tracks()
        lefts = min(r[0] for r in boxes)
        cands.append(lefts - H_GAP / 2.0)
        xt = next((c for c in cands
                   if _track_free(c, min(exit_cy, bus_y), max(exit_cy, bus_y),
                                  [r for n, r in zip(pos, boxes) if n not in (a, b)], "x")),
                  cands[-1])
        return _dedup([p0, (p0[0], exit_cy), (xt, exit_cy), (xt, bus_y),
                       (p1[0], bus_y), p1])
    p0 = _port(pos, sizes, a, "top")
    p1 = _port(pos, sizes, b, "bottom")
    exit_cy = channel_before(la)
    bus_y = channel_after(lb)
    if abs(exit_cy - bus_y) < 1e-9:
        return _dedup([p0, (p0[0], bus_y), (p1[0], bus_y), p1])
    cands = [p0[0], p1[0]] + gap_tracks()
    rights = max(r[0] + r[2] for r in boxes)
    cands.append(rights + H_GAP / 2.0)
    xt = next((c for c in cands
               if _track_free(c, min(exit_cy, bus_y), max(exit_cy, bus_y),
                              [r for n, r in zip(pos, boxes) if n not in (a, b)], "x")),
              cands[-1])
    return _dedup([p0, (p0[0], exit_cy), (xt, exit_cy), (xt, bus_y),
                   (p1[0], bus_y), p1])


def _dedup(pts):
    out = []
    for p in pts:
        q = (round(p[0], 1), round(p[1], 1))
        if not out or abs(out[-1][0] - q[0]) > 1e-9 or abs(out[-1][1] - q[1]) > 1e-9:
            out.append(q)
    return out


def route_all(spec, pos, sizes, aux):
    """Dinh tuyen moi canh; canh cung dich chia se bus doc/ngang o kenh vao."""
    routes = {}
    for i, e in enumerate(spec.get("edges", [])):
        if aux["direction"] == "LR":
            routes[i] = _route_lr(e["from"], e["to"], pos, sizes, aux)
        else:
            routes[i] = _route_tb(e["from"], e["to"], pos, sizes, aux)
    return routes


def branch_dots(spec, routes, aux):
    """Diem re nhanh: noi canh gop vao bus chung truoc khi vao dich."""
    incoming = {}
    for i, e in enumerate(spec.get("edges", [])):
        incoming.setdefault(e["to"], []).append(i)
    dots = []
    for _dst, idxs in incoming.items():
        if len(idxs) < 2:
            continue
        for i in idxs:
            pts = routes[i]
            if len(pts) >= 4:
                for q in pts[1:-1]:
                    if q not in dots and q != pts[-2]:
                        dots.append(q)
                        break
    return dots


# ---------------------------------------------------------------- hop noi dung (viewBox khit)
def content_bbox(spec, pos, sizes, routes, legend_lines=(), lanes=()):
    xs, ys = [], []
    for nid in pos:
        x, y = pos[nid]
        w, h = sizes[nid]
        xs += [x, x + w]
        ys += [y, y + h]
    for pts in routes.values():
        for (px, py) in pts:
            xs.append(px)
            ys.append(py)
    if lanes:
        for info in lane_band_rects(spec, pos, sizes, lanes):
            xs += [info[0], info[0] + info[2]]
            ys += [info[1], info[1] + info[3]]
    if legend_shown_lines(spec):
        lx, ly, lw, lh = legend_box(spec, pos, sizes, legend_lines, lanes)
        xs += [lx, lx + lw]
        ys += [ly, ly + lh]
    if not xs:
        return (0.0, 0.0, 100.0, 100.0)
    return (min(xs), min(ys), max(xs), max(ys))


def lane_band_rects(spec, pos, sizes, lanes):
    """Khung nen tung lane: (x, y, w, h, lane, bg, fg)."""
    groups = {}
    for n in spec["nodes"]:
        groups.setdefault(lane_of(n), []).append(n["id"])
    rects = []
    for lane in (lanes or sorted(groups)):
        members = groups.get(lane, [])
        if not members:
            continue
        bg, fg = lane_color(lane, lanes or sorted(groups))
        x0 = min(pos[m][0] for m in members) - 14
        y0 = min(pos[m][1] for m in members) - 30
        x1 = max(pos[m][0] + sizes[m][0] for m in members) + 14
        y1 = max(pos[m][1] + sizes[m][1] for m in members) + 14
        rects.append((x0, y0, x1 - x0, y1 - y0, lane, bg, fg))
    return rects


def legend_box(spec, pos, sizes, legend_lines, lanes):
    """Hop chu thich dat duoi noi dung: (x, y, w, h)."""
    lines = list(legend_lines)
    if lanes:
        for lane in lanes:
            if any(lane_of(n) == lane for n in spec["nodes"]):
                lines.append("■ " + lane)
    if not lines:
        lines = [""]
    lw = max([text_width(t, LEGEND_FS) for t in lines] + [40.0]) + 24
    lh = len(lines) * 16 + 20
    if pos:
        x0 = min(p[0] for p in pos.values())
        y1 = max(p[1] + sizes[n][1] for n, p in pos.items())
    else:
        x0, y1 = 0.0, 0.0
    return (x0, y1 + 16, lw, lh)


def legend_lines_of(spec, dropped=()):
    lines = []
    if spec.get("legend"):
        lines += wrap_text(spec["legend"], LEGEND_FS, 560)
    if dropped:
        lines.append("Noi sang phan khac: %s"
                     % ", ".join("%s->%s" % (a, b) for a, b in dropped[:12]))
        if len(dropped) > 12:
            lines.append("... va %d canh nua" % (len(dropped) - 12))
    return lines


def legend_shown_lines(spec):
    """Cac dong chu thich se ve (legend + chip lane); rong = khong ve hop."""
    lines = list(legend_lines_of(spec))
    lanes = spec.get("lanes") or []
    if lanes:
        lines += ["■ " + l for l in lanes
                  if any(lane_of(n) == l for n in spec["nodes"])]
    return lines


# ---------------------------------------------------------------- excalidraw elements
_uid = [0]


def _new_id(prefix):
    _uid[0] += 1
    return "%s%d" % (prefix, _uid[0])


def shape_element(nid, n, x, y, w=None, h=None):
    kind = norm_kind(n.get("kind", "default"))
    bg, fg = PALETTE[kind]
    gate = is_gate_kind(n.get("kind", "default"))
    w = NODE_W if w is None else w
    h = NODE_H if h is None else h
    text_id = "t_%s" % nid
    shape = {
        "id": nid, "type": "rectangle", "x": x, "y": y,
        "width": w, "height": h, "angle": 0,
        "strokeColor": fg, "backgroundColor": bg, "fillStyle": "solid",
        "strokeWidth": 2 if gate else 1,
        "strokeStyle": "dashed" if gate else "solid",
        "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": {"type": 3}, "boundElements": [{"type": "text", "id": text_id}],
        "link": None, "locked": False,
    }
    return shape, text_id


def text_element(tid, shape_id, x, y, label, note=None, w=None, h=None):
    w = NODE_W if w is None else w
    h = NODE_H if h is None else h
    full = label if not note else "%s\n%s" % (label, note)
    lines = full.split("\n")
    tw = max([text_width(s, 16) for s in lines] + [40.0]) + 8
    tw = min(w - TEXT_PAD_X, tw)
    th = max(20, len(lines) * 20)
    th = min(h - 8, th)
    return {
        "id": tid, "type": "text",
        "x": round(x + (w - tw) / 2, 1), "y": round(y + (h - th) / 2, 1),
        "width": round(tw, 1), "height": th, "angle": 0,
        "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
        "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "boundElements": [],
        "link": None, "locked": False,
        "text": full, "fontSize": 16, "fontFamily": 1,
        "textAlign": "center", "verticalAlign": "middle",
        "containerId": shape_id, "originalText": full, "lineHeight": 1.25,
    }


def arrow_element(aid, a, b, p1, p2, label=None, dashed=False, points=None):
    if points is not None and len(points) >= 2:
        x1, y1 = points[0]
        rel = [[0, 0]] + [[round(px - x1, 1), round(py - y1, 1)] for (px, py) in points[1:]]
    else:
        x1, y1 = p1
        x2, y2 = p2
        rel = [[0, 0], [round(x2 - x1, 1), round(y2 - y1, 1)]]
    elems = [{
        "id": aid, "type": "arrow", "x": x1, "y": y1,
        "width": abs(rel[-1][0]), "height": abs(rel[-1][1]), "angle": 0,
        "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 1,
        "strokeStyle": "dashed" if dashed else "solid",
        "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "boundElements": [],
        "link": None, "locked": False,
        "points": rel,
        "lastCommittedPoint": None,
        "startBinding": {"elementId": a, "focus": 0, "gap": 1},
        "endBinding": {"elementId": b, "focus": 0, "gap": 1},
        "startArrowhead": None, "endArrowhead": "arrow",
        "elbowed": True,
    }]
    if label:
        tid = "t_%s" % aid
        elems[0]["boundElements"] = [{"type": "text", "id": tid}]
        if points is not None and len(points) >= 2:
            mx, my = _path_midpoint(points)
        else:
            mx, my = (x1 + (x1 + rel[-1][0])) / 2, (y1 + (y1 + rel[-1][1])) / 2
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


def _path_midpoint(pts):
    """Diem giua duong gap khuc theo do dai (dat nhan canh)."""
    total = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                for i in range(len(pts) - 1))
    if total <= 1e-9:
        return pts[0]
    target, acc = total / 2.0, 0.0
    for i in range(len(pts) - 1):
        seg = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        if acc + seg >= target and seg > 1e-9:
            t = (target - acc) / seg
            return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t,
                    pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t)
        acc += seg
    return pts[-1]


def build_excalidraw(spec, pos):
    _uid[0] = 0
    _pos = {k: (float(v[0]), float(v[1])) for k, v in pos.items()}
    sizes = {n["id"]: node_size(n) for n in spec["nodes"]}
    _o, _l = topo_layers(spec["nodes"], spec.get("edges", []))
    aux_dir = (spec.get("direction") or "LR").upper()
    _p2, _s2, _w2, _h2, aux = layout_full(spec)
    aux["direction"] = aux_dir
    routes = route_all(spec, _pos, sizes, aux)
    by_id = {n["id"]: n for n in spec["nodes"]}
    elements = []
    for nid, n in by_id.items():
        x, y = _pos[nid]
        w, h = sizes[nid]
        shape, tid = shape_element(nid, n, x, y, w, h)
        elements.append(shape)
        label_lines, note_lines = node_text_lines(n)
        elements.append(text_element(tid, nid, x, y, "\n".join(label_lines),
                                     "\n".join(note_lines) if note_lines else None, w, h))
    for i, e in enumerate(spec.get("edges", [])):
        pts = routes.get(i) or [_pos[e["from"]], _pos[e["to"]]]
        elements.extend(arrow_element("e%d_%s_%s" % (i, e["from"], e["to"]),
                                      e["from"], e["to"], pts[0], pts[-1],
                                      e.get("label"), (e.get("style") or "solid") == "dashed",
                                      points=pts))
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
    _pos = {k: (float(v[0]), float(v[1])) for k, v in pos.items()}
    by_id = {n["id"]: n for n in spec["nodes"]}
    sizes = {nid: node_size(n) for nid, n in by_id.items()}
    _p2, _s2, _w2, _h2, aux = layout_full(spec)
    routes = route_all(spec, _pos, sizes, aux)
    lanes = spec.get("lanes") or []
    legend_lines = legend_lines_of(spec)
    dots = branch_dots(spec, routes, aux)
    bands = lane_band_rects(spec, _pos, sizes, lanes) if lanes else []
    lx, ly, lw, lh = legend_box(spec, _pos, sizes, legend_lines, lanes)
    minx, miny, maxx, maxy = content_bbox(spec, _pos, sizes, routes, legend_lines, lanes)
    vx, vy = minx - VIEW_MARGIN, miny - VIEW_MARGIN
    vw, vh = (maxx - minx) + VIEW_MARGIN * 2, (maxy - miny) + VIEW_MARGIN * 2
    parts = ['<svg viewBox="%s %s %s %s" xmlns="http://www.w3.org/2000/svg" role="img">'
             % (_num(vx), _num(vy), _num(vw), _num(vh)),
             '<!-- %s -->' % DIAGRAM_VERSION,
             '<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="7" '
             'refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#1e1e1e"/>'
             "</marker></defs>",
             '<rect x="%s" y="%s" width="%s" height="%s" fill="#ffffff"/>'
             % (_num(vx), _num(vy), _num(vw), _num(vh)),
             "<title>%s</title>" % E(spec.get("title", ""))]
    for (bx, by, bw, bh, lane, bg, fg) in bands:
        parts.append('<rect x="%s" y="%s" width="%s" height="%s" rx="10" fill="%s" '
                     'fill-opacity="0.35" stroke="%s" stroke-width="1.2" '
                     'stroke-dasharray="7 4"/>'
                     % (_num(bx), _num(by), _num(bw), _num(bh), bg, fg))
        parts.append('<text x="%s" y="%s" font-size="%d" font-weight="700" fill="%s" '
                     'font-family="system-ui,sans-serif">%s</text>'
                     % (_num(bx + 12), _num(by + 20), LANE_FS, fg, E("▸ " + lane)))
    for i, e in enumerate(spec.get("edges", [])):
        pts = routes.get(i) or [_pos[e["from"]], _pos[e["to"]]]
        dash = ' stroke-dasharray="6 4"' if (e.get("style") or "solid") == "dashed" else ""
        flat = " ".join("%s,%s" % (_num(px), _num(py)) for (px, py) in pts)
        parts.append('<polyline points="%s" fill="none" '
                     'stroke="#1e1e1e" stroke-width="1.6" marker-end="url(#ar)"%s/>'
                     % (flat, dash))
        if e.get("label"):
            mx, my = _path_midpoint(pts)
            lw_e = text_width(e["label"], EDGE_FS) + 10
            parts.append('<rect x="%s" y="%s" width="%s" height="18" rx="4" fill="#ffffff"/>'
                         % (_num(mx - lw_e / 2), _num(my - 9), _num(lw_e)))
            parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="%d" '
                         'fill="#333" font-family="system-ui,sans-serif">%s</text>'
                         % (_num(mx), _num(my + 4), EDGE_FS, E(e["label"])))
    for (dx, dy) in dots:
        parts.append('<circle cx="%s" cy="%s" r="3.2" fill="#1e1e1e"/>' % (_num(dx), _num(dy)))
    for nid, n in by_id.items():
        x, y = _pos[nid]
        w, h = sizes[nid]
        kind = norm_kind(n.get("kind", "default"))
        bg, fg = PALETTE[kind]
        gate = is_gate_kind(n.get("kind", "default"))
        dash = ' stroke-dasharray="8 5"' if gate else ""
        rx = 14 if gate else 8
        sw = 2 if gate else 1.5
        parts.append('<rect x="%s" y="%s" width="%s" height="%s" rx="%d" fill="%s" '
                     'stroke="%s" stroke-width="%s"%s/>'
                     % (_num(x), _num(y), _num(w), _num(h), rx, bg, fg, sw, dash))
        label_lines, note_lines = node_text_lines(n)
        total_h = len(label_lines) * LABEL_LH + (len(note_lines) * NOTE_LH + 6
                                                 if note_lines else 0)
        ty = y + (h - total_h) / 2 + LABEL_LH - 4
        for ln in label_lines:
            parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="%d" '
                         'font-weight="600" fill="#1a1a1a" '
                         'font-family="system-ui,sans-serif">%s</text>'
                         % (_num(x + w / 2), _num(ty), LABEL_FS, E(ln)))
            ty += LABEL_LH
        if note_lines:
            ty += 6
            for ln in note_lines:
                parts.append('<text x="%s" y="%s" text-anchor="middle" font-size="%d" '
                             'fill="#444" font-family="system-ui,sans-serif">%s</text>'
                             % (_num(x + w / 2), _num(ty), NOTE_FS, E(ln)))
                ty += NOTE_LH
    all_legend = legend_shown_lines(spec)
    if all_legend:
        shown = all_legend
        parts.append('<rect x="%s" y="%s" width="%s" height="%s" rx="6" fill="#f8f9fa" '
                     'stroke="#adb5bd" stroke-width="1"/>'
                     % (_num(lx), _num(ly), _num(lw), _num(lh)))
        for j, ln in enumerate(shown):
            fill = lane_color(ln[2:], lanes)[1] if ln.startswith("■ ") else "#666"
            parts.append('<text x="%s" y="%s" font-size="%d" fill="%s" '
                         'font-family="system-ui,sans-serif">%s</text>'
                         % (_num(lx + 12), _num(ly + 18 + j * 16), LEGEND_FS, fill, E(ln)))
    return "".join(parts) + "</svg>"


def _num(v):
    v = round(float(v), 1)
    if v == int(v):
        return str(int(v))
    return str(v)


def viewbox_of_svg(svg):
    """Lay (x, y, w, h) tu viewBox cua SVG."""
    import re
    m = re.search(r'viewBox="([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)"', svg)
    if not m:
        return None
    return tuple(float(g) for g in m.groups())


def viewbox_tightness(spec, pos, svg):
    """Ti le dien tich noi dung / viewBox (cang gan 1 cang khit)."""
    _pos = {k: (float(v[0]), float(v[1])) for k, v in pos.items()}
    sizes = {n["id"]: node_size(n) for n in spec["nodes"]}
    _p2, _s2, _w2, _h2, aux = layout_full(spec)
    routes = route_all(spec, _pos, sizes, aux)
    lanes = spec.get("lanes") or []
    minx, miny, maxx, maxy = content_bbox(spec, _pos, sizes, routes,
                                          legend_lines_of(spec), lanes)
    vb = viewbox_of_svg(svg)
    if vb is None:
        return 0.0
    _vx, _vy, vw, vh = vb
    if vw <= 0 or vh <= 0:
        return 0.0
    return ((maxx - minx) * (maxy - miny)) / (vw * vh)


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


def arrow_abs_points(el):
    """Toa do tuyet doi cua duong mui ten."""
    pts = el.get("points") or []
    return [(el["x"] + p[0], el["y"] + p[1]) for p in pts]


def seg_crosses_rect(x1, y1, x2, y2, rx, ry, rw, rh):
    """Doan thang co cat NOI THAT hop khong (cham bien/tiep xuc thi khong)."""
    eps = 1e-9
    if rx + eps < x1 < rx + rw - eps and ry + eps < y1 < ry + rh - eps:
        return True
    if rx + eps < x2 < rx + rw - eps and ry + eps < y2 < ry + rh - eps:
        return True
    # Liang-Barsky tren hop co lai (bien mo): tiep bien cho qua.
    ix0, iy0, ix1, iy1 = rx + eps, ry + eps, rx + rw - eps, ry + rh - eps
    if ix0 >= ix1 or iy0 >= iy1:
        return False
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - ix0), (dx, ix1 - x1), (-dy, y1 - iy0), (dy, iy1 - y1)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 - t1 > 1e-9:
                return False
    return (t1 - t0) > 1e-9 and t1 > 1e-9 and t0 < 1 - 1e-9


def rects_overlap(a, b):
    """Hai hop co chong nhau (dien tich > 0; cham bien thi khong)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return min(ax + aw, bx + bw) - max(ax, bx) > 1e-9 and \
        min(ay + ah, by + bh) - max(ay, by) > 1e-9


def point_to_path_dist(px, py, pts):
    """Khoang cach tu diem toi duong gap khuc."""
    best = float("inf")
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        if seg2 < 1e-12:
            best = min(best, math.hypot(px - x1, py - y1))
            continue
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg2))
        best = min(best, math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)))
    return best


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
            mids = edge_midpoints(r["x"], r["y"], r.get("width", NODE_W),
                                  r.get("height", NODE_H))
            if not all(_finite(v) for v in (px, py, r["x"], r["y"])):
                errs.append("mui ten '%s' %s toa do khong huu han" % (el.get("id"), which))
                continue
            if min(math.hypot(px - mx, py - my) for mx, my in mids) > TOL + 1e-9:
                errs.append("mui ten '%s' %s lech mep hop '%s' (diem [%.1f, %.1f])"
                            % (el.get("id"), which, ref, px, py))
    # Hinh hoc moi (v0.2): mui ten khong xuyen hop la.
    for el in els:
        if el.get("type") != "arrow":
            continue
        pts = el.get("points")
        if not (isinstance(pts, list) and len(pts) >= 2):
            continue
        sb = (el.get("startBinding") or {}).get("elementId")
        eb = (el.get("endBinding") or {}).get("elementId")
        path = arrow_abs_points(el)
        for rid, r in rects.items():
            if rid in (sb, eb):
                continue
            if not all(_finite(v) for v in (r.get("x"), r.get("y"),
                                            r.get("width"), r.get("height"))):
                continue
            hit = False
            for i in range(len(path) - 1):
                if seg_crosses_rect(path[i][0], path[i][1], path[i + 1][0], path[i + 1][1],
                                    r["x"], r["y"], r["width"], r["height"]):
                    hit = True
                    break
            if hit:
                errs.append("mui ten '%s' xuyen hop '%s' (cat noi that hop la)"
                            % (el.get("id"), rid))
    # Hinh hoc moi (v0.2): hai hop khong chong nhau.
    rids = list(rects)
    for i in range(len(rids)):
        for j in range(i + 1, len(rids)):
            a, b = rects[rids[i]], rects[rids[j]]
            if not all(_finite(v) for v in (a.get("x"), a.get("y"), a.get("width"), a.get("height"),
                                            b.get("x"), b.get("y"), b.get("width"), b.get("height"))):
                continue
            if rects_overlap((a["x"], a["y"], a["width"], a["height"]),
                             (b["x"], b["y"], b["width"], b["height"])):
                errs.append("hop chong nhau: '%s' & '%s'" % (rids[i], rids[j]))
    # Hinh hoc moi (v0.2): moi chu nam tron trong hop chua no.
    for el in els:
        if el.get("type") != "text" or not el.get("containerId"):
            continue
        host = seen.get(el["containerId"])
        if host is None:
            continue
        if host.get("type") == "rectangle":
            if not all(_finite(v) for v in (el.get("x"), el.get("y"), el.get("width"),
                                            el.get("height"), host.get("x"), host.get("y"),
                                            host.get("width"), host.get("height"))):
                continue
            inside = (el["x"] >= host["x"] - TEXT_TOL
                      and el["y"] >= host["y"] - TEXT_TOL
                      and el["x"] + el["width"] <= host["x"] + host["width"] + TEXT_TOL
                      and el["y"] + el["height"] <= host["y"] + host["height"] + TEXT_TOL)
            if not inside:
                errs.append("chu tran hop: text '%s' tran '%s'" % (el.get("id"),
                                                                  host.get("id")))
        elif host.get("type") == "arrow":
            pts = host.get("points")
            if not (isinstance(pts, list) and len(pts) >= 2):
                continue
            if not all(_finite(v) for v in (el.get("x"), el.get("y"), el.get("width"),
                                            el.get("height"))):
                continue
            cx = el["x"] + el["width"] / 2.0
            cy = el["y"] + el["height"] / 2.0
            if point_to_path_dist(cx, cy, arrow_abs_points(host)) > ARROW_LABEL_NEAR:
                errs.append("chu tran duong mui ten: text '%s' tran '%s'"
                            % (el.get("id"), host.get("id")))
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


def _chunk_by_topo(spec, max_nodes):
    nodes = spec["nodes"]
    order, _layer = topo_layers(nodes, spec.get("edges", []))
    rank = {nid: i for i, nid in enumerate(order)}
    ordered = sorted(nodes, key=lambda n: rank[n["id"]])
    return [ordered[i:i + max_nodes] for i in range(0, len(ordered), max_nodes)]


def _sub_spec(spec, chunk, idx, total):
    keep = {n["id"] for n in chunk}
    sub = copy.deepcopy(spec)
    sub.pop("_overview", None)
    sub["nodes"] = chunk
    sub["edges"] = [e for e in spec.get("edges", [])
                    if e["from"] in keep and e["to"] in keep]
    sub["title"] = "%s (phan %d/%d)" % (spec.get("title", "diagram"), idx, total)
    return sub, [(e["from"], e["to"]) for e in spec.get("edges", [])
                 if not (e["from"] in keep and e["to"] in keep)]


def overview_spec(spec, groups):
    """So do tong quan: moi nhom (phase/phan) la 1 hop, canh la phu thuoc nhom."""
    inv = {}
    for gi, (_gname, ids) in enumerate(groups):
        for nid in ids:
            inv[nid] = gi
    gedges, seen_e = [], set()
    for e in spec.get("edges", []):
        ga, gb = inv.get(e["from"]), inv.get(e["to"])
        if ga is None or gb is None or ga == gb:
            continue
        if (ga, gb) not in seen_e:
            seen_e.add((ga, gb))
            gedges.append({"from": "ov%d" % ga, "to": "ov%d" % gb})
    kinds = ["data", "service", "ai", "evaluation", "artifact", "api"]
    nodes = [{"id": "ov%d" % gi, "label": "%s (%d task)" % (gname, len(ids)),
              "kind": kinds[gi % len(kinds)], "note": "nhom %d/%d" % (gi + 1, len(groups))}
             for gi, (gname, ids) in enumerate(groups)]
    ov = {"title": "%s (tong quan)" % spec.get("title", "diagram"),
          "direction": spec.get("direction", "LR"),
          "lanes": [gname for gname, _ids in groups],
          "nodes": nodes, "edges": gedges,
          "legend": "Moi hop la 1 nhom/phase; chi tiet o so do con",
          "_overview": True}
    for n in ov["nodes"]:
        n["lane"] = ov["lanes"][int(n["id"][2:])]
    return ov


def split_spec(spec):
    """Tach spec >25 node: theo phase (moi phase <=25) + so do tong quan.

    Khong phase thi tach theo topo (giua lai canh noi bo) + tong quan cac phan.
    """
    nodes = spec["nodes"]
    if len(nodes) <= MAX_NODES:
        return [spec]
    lanes = spec.get("lanes") or []
    Locale = sorted({lane_of(n) for n in nodes})
    if len(Locale) >= 2 and lanes:
        order = [l for l in lanes if l in Locale] + [l for l in Locale if l not in lanes]
        groups = []
        for lane in order:
            members = [n for n in nodes if lane_of(n) == lane]
            if len(members) <= MAX_NODES:
                groups.append((lane, members))
            else:
                for k, chunk in enumerate(_chunk_by_topo(
                        {"nodes": members,
                         "edges": [e for e in spec.get("edges", [])
                                   if lane_of(_node_by_id(spec, e["from"])) == lane
                                   and lane_of(_node_by_id(spec, e["to"])) == lane]},
                        MAX_NODES)):
                    groups.append(("%s-%d" % (lane, k + 1), chunk))
        parts, dropped = [], []
        for idx, (gname, chunk) in enumerate(groups, 1):
            sub, drop = _sub_spec(spec, chunk, idx, len(groups))
            sub["title"] = "%s [%s]" % (spec.get("title", "diagram"), gname)
            parts.append(sub)
            dropped += drop
        ov = overview_spec(spec, [(g, [n["id"] for n in chunk]) for g, chunk in groups])
        out = [ov] + parts
    else:
        chunks = _chunk_by_topo(spec, MAX_NODES)
        parts, dropped = [], []
        for idx, chunk in enumerate(chunks, 1):
            sub, drop = _sub_spec(spec, chunk, idx, len(chunks))
            parts.append(sub)
            dropped += drop
        ov = overview_spec(spec, [("phan %d" % i, [n["id"] for n in chunk])
                                  for i, chunk in enumerate(chunks, 1)])
        out = [ov] + parts
    if dropped:
        print("canh: tach %d node thanh %d so do con, bo %d canh lien phan: %s"
              % (len(nodes), len(parts), len(dropped), dropped[:10]))
        link_note = "Noi sang phan khac: %s" % ", ".join("%s->%s" % (a, b) for a, b in dropped)
        for sub in parts:
            sub["legend"] = ((sub.get("legend") or "") + " | " + link_note).strip(" |")
    return out


def _node_by_id(spec, nid):
    for n in spec["nodes"]:
        if n["id"] == nid:
            return n
    return {"id": nid}


def render_spec(spec, out_dir, formats, name):
    spec = spec_from_json(copy.deepcopy(spec))
    os.makedirs(out_dir, exist_ok=True)
    written = []
    parts = split_spec(spec)
    counters = {}
    for part in parts:
        is_ov = bool(part.pop("_overview", False))
        if len(parts) == 1:
            base = name
        elif is_ov:
            base = name + "_overview"
        else:
            counters["p"] = counters.get("p", 0) + 1
            base = "%s_p%d" % (name, counters["p"])
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
        with open(reg, encoding="utf-8") as f:
            data = json.load(f)
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
        for d in t.get("deps", []) or []:
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
