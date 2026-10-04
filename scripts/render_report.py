#!/usr/bin/env python3
"""Render eval JSON -> report.md (Vietnamese, 3 mandatory sections, markdown only) + report.html (error clusters).

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


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _score(row, prefix=""):
    """(value in %, 'đúng/tổng' text) for current (prefix='') or previous (prefix='prev_') of a row."""
    if prefix == "":
        c, t, v = row.get("correct"), row.get("total"), _num(row.get("value"))
    else:
        c, t, v = row.get("baseline_correct"), row.get("prev_total", row.get("total")), _num(row.get("prev_value"))
    if _num(c) is not None and _num(t):
        return 100 * c / t, f"{c}/{t}"
    if v is not None:
        return (100 * v if v <= 1 else v), None
    return None, None


def _fmt(val, frac):
    if val is None:
        return "N/A"
    return f"{val:.1f}%" + (f" ({frac})" if frac else "")


def render_md(d):
    """Template: 1 Tổng quan · 2 Nội dung chi tiết (bảng chính xác + lỗi theo nhóm) · 3 Kết luận. Markdown only."""
    o, c = d["overview"], d["conclusion"]
    v = d.get("version", {})
    md = [f"# Báo cáo: {d['title']}\n",
          f"Model `{v.get('model', '?')}` · Dataset `{v.get('dataset', '?')}` · Version trước `{d.get('baseline_name', 'N/A')}`"
          + (f" · {d['round']}" if d.get("round") else "") + "\n",
          "## 1. Tổng quan\n",
          bullet("Hiện trạng bài toán", o["status"]),
          bullet("Phương pháp giải quyết", o["method"]),
          bullet("Kết quả đạt được", o["result"]),
          "\n## 2. Nội dung chi tiết\n",
          "### Bảng độ chính xác chi tiết\n",
          "Mỗi hàng là một trường của một loại tài liệu. Cùng metric và cùng tập đánh giá khi so sánh; "
          "nếu khác thì ghi rõ và không kết luận tăng/giảm. N/A = chưa có bằng chứng.\n\n"
          "| Loại tài liệu | Trường | Metric | Số mẫu | Version trước | Version hiện tại | Thay đổi |\n|---|---|---|---|---|---|---|\n"]
    for tb in d["tables"]:
        for r in tb["rows"]:
            metric = r.get("metric") or tb.get("metric") or "N/A"
            n = r.get("n") or r.get("total") or tb.get("n") or "N/A"
            cur, cur_f = _score(r)
            prv, prv_f = _score(r, "prev_")
            same = (not r.get("prev_metric") or r.get("prev_metric") == metric) and \
                   (not r.get("prev_set") or r.get("prev_set") == tb.get("eval_set", r.get("prev_set")))
            if cur is None or prv is None:
                delta = "N/A"
            elif not same:
                delta = f"không so sánh trực tiếp (trước: {r.get('prev_metric') or ''} {r.get('prev_set') or ''})".strip()
            else:
                delta = f"{cur - prv:+.1f} điểm"
            md.append(f"| {md_cell(tb['name'])} | {md_cell(r['item'])} | {md_cell(metric)} | {md_cell(n)} | "
                      f"{md_cell(_fmt(prv, prv_f))} | {md_cell(_fmt(cur, cur_f))} | {md_cell(delta)} |\n")
    md.append("\n### Phân tích lỗi theo nhóm\n\n")
    if d["errors"]:
        md.append("| Nhóm lỗi | Số lượng / tỷ lệ | Trường / loại tài liệu bị ảnh hưởng | Nguyên nhân có bằng chứng | Ví dụ tiêu biểu |\n|---|---|---|---|---|\n")
        for e in d["errors"]:
            den = e.get("denominator")
            rate = f"{e['count']}/{den} = {100 * e['count'] / den:.1f}%" if _num(den) else f"{e['count']} (mẫu số: N/A)"
            first = (e.get("examples") or [None])[0]
            ex = e.get("example") or (first["where"] + (" — " + first["note"] if first.get("note") else "") if first else "")
            md.append(f"| {md_cell(e['cluster'])} | {md_cell(rate)} | {md_cell(e.get('fields', 'N/A'))} | "
                      f"{md_cell(e.get('cause', 'N/A'))} | {md_cell(ex)} |\n")
    else:
        md.append(f"{EMPTY_ERRORS_MSG}.\n")
    md.append("\n## 3. Kết luận\n\nGiải pháp theo thứ tự ưu tiên (mức ưu tiên → nhóm lỗi → cần làm gì → cách kiểm chứng):\n\n")
    for i, f in enumerate(c["fixes"], 1):
        md.append(f"{i}. **{f['priority']}** → {f['error']} → {f['fix']} → {f['measure']}\n")
    if c.get("recommend"):
        md.append("\n" + bullet("Đề xuất dùng checkpoint/cấu hình mới", c["recommend"]))
    if d.get("artifacts"):
        md.append("\nArtifact kiểm chứng: " + "; ".join(f"[{a['label']}]({a['path']})" for a in d["artifacts"]) + "\n")
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
