#!/usr/bin/env python3
"""Render a pipeline playbook (flow + per-component weakness response) for human approval.

Usage: playbook_diagram.py <playbook.json> [--out-dir DIR]     -> playbook.html (inline SVG, light/dark) + playbook.md (Mermaid)

playbook.json (see examples/playbook.sample.json):
  {"title": "...", "flow": [["image","pre"],["pre","layout"], ...],
   "components": [{"id":"layout","name":"Layout","metric":"recall >=99%","risk":"low|med|high","why":"short reason it may be weak",
                   "checks":["learning curve", "..."],
                   "on_weak":{"DATA":"action","MODEL":"action","AUX":"action","NOISE":"action"},
                   "aux":["optional auxiliary module candidates"], "cost_cap":"e.g. 2 tasks / 1 GPU-day"}]}
Verdict names: DATA, MODEL, STRUCTURE (old name AUX accepted), OBJECTIVE, NOISE.
Stdlib only; nothing leaves the machine.
"""
import argparse
import html
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
RISK = {"low": ("#d7f0dd", "#1f7a3a"), "med": ("#fdf0cf", "#a66b00"), "high": ("#f9d9d6", "#b3261e")}
BRANCH = {"DATA": "#2563eb", "MODEL": "#7c3aed", "STRUCTURE": "#0d9488", "OBJECTIVE": "#d97706", "NOISE": "#6b7280"}


def branch_text(ow, k):
    """on_weak text for a verdict; `AUX` is the old name of STRUCTURE."""
    return ow.get(k) or (ow.get("AUX") if k == "STRUCTURE" else None) or "—"
E = html.escape


def wrap(text, n):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return lines + ([cur] if cur else [])


def svg_flow(pb):
    for c in pb["components"]:  # tolerate placeholders such as "low | med | high"
        if c.get("risk") not in RISK:
            c["risk"] = "med"
    comps = {c["id"]: c for c in pb["components"]}
    order = []
    for a, b in pb["flow"]:
        for x in (a, b):
            if x not in order:
                order.append(x)
    per_row, bw, bh, gx, gy = 4, 190, 78, 38, 46
    pos = {}
    for i, n in enumerate(order):
        r, c = divmod(i, per_row)
        c = c if r % 2 == 0 else per_row - 1 - c  # snake layout keeps arrows short
        pos[n] = (20 + c * (bw + gx), 20 + r * (bh + gy))
    rows = (len(order) - 1) // per_row + 1
    W, H = 40 + per_row * bw + (per_row - 1) * gx, 40 + rows * bh + (rows - 1) * gy
    out = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="pipeline flow">'
           '<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor"/></marker></defs>']
    for a, b in pb["flow"]:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        if y1 == y2:
            sx, ex = (x1 + bw, x2) if x2 > x1 else (x1, x2 + bw)
            out.append(f'<line x1="{sx}" y1="{y1 + bh / 2}" x2="{ex}" y2="{y2 + bh / 2}" stroke="currentColor" stroke-width="1.6" marker-end="url(#ar)"/>')
        else:
            out.append(f'<line x1="{x1 + bw / 2}" y1="{y1 + bh}" x2="{x2 + bw / 2}" y2="{y2}" stroke="currentColor" stroke-width="1.6" marker-end="url(#ar)"/>')
    for n in order:
        x, y = pos[n]
        c = comps.get(n)
        fill, stroke = RISK[c["risk"]] if c else ("#e8eaf0", "#6b7280")
        label = c["name"] if c else n
        metric = c["metric"] if c else ""
        out.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        for i, ln in enumerate(wrap(label, 24)[:2]):
            out.append(f'<text x="{x + bw / 2}" y="{y + 25 + i * 16}" text-anchor="middle" font-size="13" font-weight="600" fill="#1a1a1a">{E(ln)}</text>')
        for i, ln in enumerate(wrap(metric, 28)[:2]):
            out.append(f'<text x="{x + bw / 2}" y="{y + 56 + i * 13}" text-anchor="middle" font-size="11" fill="#333">{E(ln)}</text>')
    return "".join(out) + "</svg>"


def svg_decision():
    w, h = 860, 320
    s = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="weakness decision flow">'
         '<defs><marker id="ar2" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor"/></marker></defs>']

    def box(x, y, bw, bh, t, fill, stroke, tc="#1a1a1a"):
        s.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="9" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        for i, ln in enumerate(wrap(t, int(bw / 7.2))[:4]):
            s.append(f'<text x="{x + bw / 2}" y="{y + 22 + i * 15}" text-anchor="middle" font-size="12" fill="{tc}">{E(ln)}</text>')

    def arrow(x1, y1, x2, y2):
        s.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="currentColor" stroke-width="1.6" marker-end="url(#ar2)"/>')

    box(10, 125, 150, 56, "Thành phần dưới mục tiêu (sau vòng train đầu)", "#fdf0cf", "#a66b00")
    box(200, 113, 205, 80, "Agent chẩn đoán: thí nghiệm phân biệt (learning curve, lát cắt, oracle, capacity, đọc mù)", "#e8eaf0", "#6b7280")
    arrow(160, 153, 200, 153)
    items = [("DATA", "Ít dữ liệu thật / thiếu phủ lát cắt / data sinh lệch phân bố → xin data thật, làm sạch nhãn"),
             ("MODEL", "Thiếu năng lực (đối tượng nhỏ, độ phân giải, ngữ cảnh) → chỉnh/cải thiện model"),
             ("STRUCTURE", "Cần tách cấu trúc / module phụ (oracle tăng mạnh) → thêm task module"),
             ("OBJECTIVE", "Metric/ngưỡng/spec lệch → chỉnh cùng người dùng"),
             ("NOISE", "Nhãn mơ hồ → hạ mục tiêu / chấp nhận khoảng cách")]
    for i, (k, t) in enumerate(items):
        y = 8 + i * 60
        arrow(405, 153, 470, y + 26)
        box(470, y, 380, 52, f"{k}: {t}", "#ffffff", BRANCH[k])
    return "".join(s) + "</svg>"


def mermaid(pb):
    comps = {c["id"]: c for c in pb["components"]}
    lines = ["```mermaid", "flowchart LR"]
    for a, b in pb["flow"]:
        lines.append(f"  {a}[\"{comps.get(a, {}).get('name', a)}\"] --> {b}[\"{comps.get(b, {}).get('name', b)}\"]")
    lines += ["  classDef low fill:#d7f0dd,stroke:#1f7a3a;", "  classDef med fill:#fdf0cf,stroke:#a66b00;", "  classDef high fill:#f9d9d6,stroke:#b3261e;"]
    for c in pb["components"]:
        lines.append(f"  class {c['id']} {c['risk']};")
    lines += ["  W[\"Thành phần yếu\"] --> D[\"Agent chẩn đoán\"]", "  D --> DATA[\"DATA: xin data thật / làm sạch nhãn\"]",
              "  D --> MODEL[\"MODEL: chỉnh/cải thiện model\"]", "  D --> STRUCTURE[\"STRUCTURE: tách cấu trúc / module phụ\"]", "  D --> OBJECTIVE[\"OBJECTIVE: chỉnh metric/ngưỡng/spec\"]", "  D --> NOISE[\"NOISE: hạ mục tiêu\"]", "```"]
    return "\n".join(lines)


def render(pb):
    rows = []
    for c in pb["components"]:
        ow = c.get("on_weak", {})
        cells = "".join(f'<td><b style="color:{BRANCH[k]}">{k}</b> {E(branch_text(ow, k))}</td>' for k in BRANCH)
        aux = f'<div class="m">Module phụ ứng viên: {E(", ".join(c["aux"]))}</div>' if c.get("aux") else ""
        chk = f'<div class="m">Kiểm tra trước: {E("; ".join(c.get("checks", [])))}</div>'
        rows.append(f'<tr class="r-{c["risk"]}"><td><b>{E(c["name"])}</b><div class="m">{E(c["metric"])} · rủi ro {E(c["risk"])}</div>'
                    f'<div class="m">{E(c.get("why", ""))}</div>{chk}{aux}<div class="m">Trần chi phí: {E(c.get("cost_cap", "—"))}</div></td>{cells}</tr>')
    css = ("body{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px;background:#fff;color:#1a1a1a}"
           "@media(prefers-color-scheme:dark){body{background:#15171c;color:#e8e8e8}td,th{border-color:#444!important}}"
           "svg{width:100%;height:auto;color:inherit}table{border-collapse:collapse;width:100%;font-size:13px}"
           "td,th{border:1px solid #ccc;padding:6px;vertical-align:top}.m{color:#777;font-size:12px}.legend span{display:inline-block;padding:2px 8px;margin-right:6px;border-radius:5px}"
           "@media(max-width:700px){table{display:block;overflow-x:auto}}")
    return (f'<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{E(pb["title"])} — playbook</title><style>{css}</style></head><body><h1>{E(pb["title"])}</h1>'
            '<p>Duyệt tại G2: (1) luồng pipeline và mức rủi ro từng thành phần, (2) cách xử lý khi một thành phần yếu: '
            'agent chẩn đoán xác minh nguyên nhân rồi chọn nhánh hành động trong trần chi phí đã duyệt.</p>'
            '<div class="legend"><span style="background:#d7f0dd">rủi ro thấp</span><span style="background:#fdf0cf">trung bình</span><span style="background:#f9d9d6">cao</span></div>'
            f'<h2>1. Luồng pipeline</h2>{svg_flow(pb)}<h2>2. Khi một thành phần yếu</h2>{svg_decision()}'
            f'<h2>3. Hành động đã lên sẵn theo thành phần</h2><table><tr><th>Thành phần</th>{"".join(f"<th>{k}</th>" for k in BRANCH)}</tr>{"".join(rows)}</table></body></html>')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("playbook")
    ap.add_argument("--out-dir")
    a = ap.parse_args()
    pb = json.load(open(a.playbook, encoding="utf-8"))
    out = a.out_dir or os.path.dirname(os.path.abspath(a.playbook))
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "playbook.html"), "w", encoding="utf-8").write(render(pb))
    md = [f"# {pb['title']} — playbook\n", "Duyệt tại G2: luồng pipeline + cách xử lý khi một thành phần yếu.\n", mermaid(pb), "\n## Hành động đã lên sẵn\n"]
    for c in pb["components"]:
        md.append(f"### {c['name']} ({c['metric']}, rủi ro {c['risk']})\n- Vì sao có thể yếu: {c.get('why', '—')}\n- Kiểm tra trước: {'; '.join(c.get('checks', []))}")
        for k in BRANCH:
            md.append(f"- **{k}** → {branch_text(c.get('on_weak', {}), k)}")
        if c.get("aux"):
            md.append("- Module phụ ứng viên: " + ", ".join(c["aux"]))
        md.append(f"- Trần chi phí: {c.get('cost_cap', '—')}\n")
    open(os.path.join(out, "playbook.md"), "w", encoding="utf-8").write("\n".join(md))
    print("wrote", os.path.join(out, "playbook.html"), "and playbook.md")


if __name__ == "__main__":
    main()
