#!/usr/bin/env python3
"""Render eval JSON -> report.md (Vietnamese, 3 mandatory sections) + report.html (error clusters).

Usage: render_report.py eval.json [--out-dir DIR]
Schema: see examples/eval.sample.json and templates/report.template.md.
"""
import argparse
import sys
import html
import json
import os


sys.stdout.reconfigure(encoding="utf-8")  # Windows pipes default to cp1252
sys.stderr.reconfigure(encoding="utf-8")


def pct(c, t):
    return f"{100 * c / t:.1f}%" if t else "n/a"


def bullet(label, text):
    return f"- **{label}:** {text}\n"


EMPTY_ERRORS_MSG = "Không còn lỗi được ghi nhận"


def md_cell(value):
    """Escape a dynamic value for a Markdown table cell (pipe + newlines)."""
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def render_md(d):
    o, c = d["overview"], d["conclusion"]
    v = d.get("version", {})
    md = [f"# Báo cáo: {d['title']}\n",
          f"Model `{v.get('model', '?')}` · Dataset `{v.get('dataset', '?')}` · Baseline `{d.get('baseline_name', '-')}`\n",
          "## 1. Tổng quan\n",
          *( [bullet("Vòng thí nghiệm", d["round"])] if d.get("round") else [] ),
          bullet("Hiện trạng bài toán", o["status"]),
          bullet("Thí nghiệm thế nào", o["method"]),
          bullet("Giải quyết được vấn đề gì", o.get("solved") or "_(chưa ghi: vòng này chưa giải quyết được vấn đề nào hoặc người viết bỏ sót — điền `overview.solved`)_"),
          bullet("Kết quả", o["result"]),
          "\n## 2. Nội dung chi tiết\n",
          "### Bảng độ chính xác chi tiết từng thành phần\n"]
    for tb in d["tables"]:
        md.append(f"\n**{tb['name']}**\n\n| Thành phần | Đúng/Tổng | % | Baseline | Δ |\n|---|---|---|---|---|\n")
        for r in tb["rows"]:
            b = r.get("baseline_correct")
            delta = f"{100 * (r['correct'] / r['total'] - b / r['total']):+.1f}" if b is not None and r["total"] else "-"
            md.append(f"| {md_cell(r['item'])} | {md_cell(r['correct'])}/{md_cell(r['total'])} "
                      f"| {pct(r['correct'], r['total'])} | "
                      f"{pct(b, r['total']) if b is not None else '-'} | {delta} |\n")
    md.append("\n### Các lỗi sai còn tồn đọng\n")
    if d["errors"]:
        md.append("\n| Nhóm lỗi | Số lượng | Ví dụ | Nguyên nhân / giả thuyết |\n|---|---|---|---|\n")
        for e in d["errors"]:
            ex = "; ".join(x["where"] for x in e.get("examples", [])[:3])
            md.append(f"| {md_cell(e['cluster'])} | {md_cell(e['count'])} | {md_cell(ex)} | {md_cell(e.get('cause', ''))} |\n")
    else:
        md.append(f"\n{EMPTY_ERRORS_MSG}\n")
    md.append("\n## 3. Kết luận\n")
    if d["errors"]:
        md.append("\n**Lỗi sai còn tồn đọng:** " + "; ".join(f"{e['cluster']} ({e['count']})" for e in d["errors"]) + "\n")
    else:
        md.append(f"\n**Lỗi sai còn tồn đọng (tổng 0):** {EMPTY_ERRORS_MSG}\n")
    md.append("\n| Lỗi | Giải pháp | Ưu tiên | Cách đo xác nhận |\n|---|---|---|---|\n")
    for f in c["fixes"]:
        md.append(f"| {md_cell(f['error'])} | {md_cell(f['fix'])} | {md_cell(f['priority'])} | {md_cell(f['measure'])} |\n")
    md.append("\n" + bullet("Đề xuất dùng checkpoint/cấu hình mới", c["recommend"]))
    return "".join(md)


def render_html(d):
    e_ = html.escape
    count_total = sum(x["count"] for x in d["errors"])
    total = count_total or 1  # only guards the bar-width denominator
    bars, cards = [], []
    for i, e in enumerate(sorted(d["errors"], key=lambda x: -x["count"])):
        w = 100 * e["count"] / total
        bars.append(f'<a class="bar" href="#c{i}"><span>{e_(e["cluster"])}</span>'
                    f'<i style="width:{w:.1f}%"></i><b>{e["count"]}</b></a>')
        ex = "".join(
            f'<li><code>{e_(x["where"])}</code> {e_(x.get("note", ""))}'
            + (f'<br><img src="{e_(x["image"])}" alt="">' if x.get("image") else "") + "</li>"
            for x in e.get("examples", []))
        cards.append(f'<section id="c{i}"><h3>{e_(e["cluster"])} <small>{e["count"]} lỗi</small></h3>'
                     f'<p>{e_(e.get("cause", ""))}</p><ul>{ex}</ul></section>')
    if count_total:
        dist = f'<h2>Phân bố nhóm lỗi ({count_total})</h2>{"".join(bars)}'
        detail = f'<h2>Chi tiết từng nhóm</h2>{"".join(cards)}'
    else:
        dist = f'<h2>Phân bố nhóm lỗi (0)</h2><p>{e_(EMPTY_ERRORS_MSG)}</p>'
        detail = ""
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e_(d['title'])} — lỗi theo cụm</title>
<style>:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--ac:#2563eb;--card:#f5f6f8}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181d;--fg:#e8e8e8;--mut:#9aa;--ac:#6ea0ff;--card:#1f222a}}}}
body{{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);max-width:900px;margin:0 auto;padding:16px}}
.bar{{display:grid;grid-template-columns:220px 1fr 40px;gap:8px;align-items:center;color:inherit;text-decoration:none;margin:4px 0}}
.bar i{{display:block;height:14px;background:var(--ac);border-radius:3px}}
section{{background:var(--card);border-radius:8px;padding:4px 16px;margin:16px 0}}small{{color:var(--mut)}}
img{{max-width:100%;border-radius:4px}}</style></head><body>
<h1>{e_(d['title'])}</h1><p>Model <code>{e_(d.get('version',{}).get('model','?'))}</code> · Dataset <code>{e_(d.get('version',{}).get('dataset','?'))}</code></p>
{dist}{detail}</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eval_json")
    ap.add_argument("--out-dir", default=".")
    a = ap.parse_args()
    with open(a.eval_json, encoding="utf-8") as f:
        d = json.load(f)
    os.makedirs(a.out_dir, exist_ok=True)
    for name, content in (("report.md", render_md(d)), ("report.html", render_html(d))):
        p = os.path.join(a.out_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        print("wrote", p)


if __name__ == "__main__":
    main()
