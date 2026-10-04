#!/usr/bin/env python3
"""Render eval JSON -> report.md (3 mandatory sections, markdown only) + report.html (error clusters).

Usage: render_report.py eval.json [--out-dir DIR] [--lang vi|en]
Schema: schemas/eval.schema.json (modality-neutral; eval OCR cũ vẫn render được,
thiếu số liệu thì N/A). Ngôn ngữ: eval.json `lang`, fallback --lang, fallback vi.
"""
import argparse
import sys
import html
import json
import os


sys.stdout.reconfigure(encoding="utf-8")  # Windows pipes default to cp1252
sys.stderr.reconfigure(encoding="utf-8")


STR = {
    "vi": {
        "sec1": "1. Tổng quan",
        "sec2": "2. Nội dung chi tiết",
        "sec3": "3. Kết luận",
        "status": "Hiện trạng bài toán",
        "method": "Phương pháp giải quyết",
        "result": "Kết quả đạt được",
        "contract": "Hợp đồng đánh giá",
        "table_title": "Bảng metric chi tiết",
        "table_intro": ("Mỗi hàng là một mục đánh giá (metric/slice; ví dụ OCR/KIE: một trường của một loại tài liệu). "
            "Cùng metric và cùng tập đánh giá khi so sánh; nếu khác thì ghi rõ và không kết luận tăng/giảm. "
            "N/A = chưa có bằng chứng."),
        "c_scope": "Phạm vi", "c_item": "Mục", "c_metric": "Metric", "c_n": "Số mẫu",
        "c_prev": "Version trước", "c_cur": "Version hiện tại", "c_delta": "Thay đổi",
        "err_title": "Phân tích lỗi theo nhóm",
        "c_cluster": "Nhóm lỗi", "c_rate": "Số lượng / tỷ lệ", "c_fields": "Phạm vi bị ảnh hưởng",
        "c_cause": "Nguyên nhân có bằng chứng", "c_ex": "Ví dụ tiêu biểu",
        "no_errors": "Không còn lỗi được ghi nhận",
        "fixes_intro": "Giải pháp theo thứ tự ưu tiên (mức ưu tiên → nhóm lỗi → cần làm gì → cách kiểm chứng):",
        "recommend": "Đề xuất dùng checkpoint/cấu hình mới",
        "artifacts": "Artifact kiểm chứng",
        "nosamples": "mẫu số: N/A",
        "nodirect": "không so sánh trực tiếp",
        "unit": "Đơn vị", "split": "Chia dữ liệu", "metrics": "Metric (hướng tốt)",
        "slices": "Lát cắt", "horizon": "Horizon", "scorer": "Chấm điểm",
        "uncertainty": "Độ bất định", "provenance": "Nguồn gốc",
    },
    "en": {
        "sec1": "1. Overview",
        "sec2": "2. Details",
        "sec3": "3. Conclusion",
        "status": "Problem status",
        "method": "Approach",
        "result": "Results",
        "contract": "Evaluation contract",
        "table_title": "Detailed metric table",
        "table_intro": ("Each row is one evaluation item (metric/slice; OCR/KIE example: one field of one document type). "
            "Compare only on the same metric and eval set; otherwise say so and draw no up/down conclusion. "
            "N/A = no evidence."),
        "c_scope": "Scope", "c_item": "Item", "c_metric": "Metric", "c_n": "N",
        "c_prev": "Previous version", "c_cur": "Current version", "c_delta": "Change",
        "err_title": "Error cluster analysis",
        "c_cluster": "Error group", "c_rate": "Count / rate", "c_fields": "Affected scope",
        "c_cause": "Evidenced cause", "c_ex": "Typical example",
        "no_errors": "No recorded errors remaining",
        "fixes_intro": "Fixes in priority order (priority → error group → action → verification):",
        "recommend": "Recommendation on new checkpoint/config",
        "artifacts": "Verification artifacts",
        "nosamples": "denominator: N/A",
        "nodirect": "not directly comparable",
        "unit": "Unit", "split": "Split", "metrics": "Metrics (better direction)",
        "slices": "Slices", "horizon": "Horizon", "scorer": "Scorer",
        "uncertainty": "Uncertainty", "provenance": "Provenance",
    },
}


def pct(c, t):
    return f"{100 * c / t:.1f}%" if t else "n/a"


def bullet(label, text):
    return f"- **{label}:** {text}\n"


EMPTY_ERRORS_MSG = {"vi": STR["vi"]["no_errors"], "en": STR["en"]["no_errors"]}


def md_cell(value):
    """Escape a dynamic value for a Markdown table cell (pipe + newlines)."""
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _score(row, prefix="", raw=False):
    """(giá trị, text 'đúng/tổng'). raw=True (hàng có unit): giữ số thô, không đổi sang %."""
    if prefix == "":
        c, t, v = row.get("correct"), row.get("total"), _num(row.get("value"))
    else:
        c, t, v = row.get("baseline_correct"), row.get("prev_total", row.get("total")), _num(row.get("prev_value"))
    if _num(c) is not None and _num(t):
        return 100 * c / t, f"{c}/{t}"
    if v is not None:
        return (v if raw else (100 * v if v <= 1 else v)), None
    return None, None


def _fmt(val, frac, ci=None, unit=None):
    if val is None:
        return "N/A"
    s = f"{val:.1f}%" + (f" ({frac})" if frac else "") if unit is None else f"{val} {unit}"
    return s + (f" [{ci}]" if ci else "")


def _metric_label(row, tb, contract_metrics):
    m = row.get("metric") or tb.get("metric")
    if not m:  # khớp item với metric trong eval_contract (vd. item "MASE h=7" ~ metric "MASE")
        item = (row.get("item") or "").lower()
        for name in contract_metrics:
            nl = name.lower()
            if item == nl or item.startswith(nl + " ") or nl.startswith(item):
                m = name
                break
    m = m or "N/A"
    d = row.get("direction") or contract_metrics.get(m)
    return m + (" ↑" if d == "higher" else " ↓" if d == "lower" else "")


def _contract_lines(ec, S):
    """Hợp đồng đánh giá thành các bullet; chỉ hiện trường có dữ liệu (thiếu => N/A ở bảng)."""
    out = []
    if ec.get("unit"):
        out.append(bullet(S["unit"], ec["unit"]))
    sp = ec.get("split") or {}
    if sp.get("strategy") or sp.get("details"):
        out.append(bullet(S["split"], " ".join(x for x in (sp.get("strategy"), sp.get("details")) if x)))
    ms = ec.get("metrics") or []
    if ms:
        out.append(bullet(S["metrics"], "; ".join(
            f"{m['name']} ({'↑' if m.get('direction') == 'higher' else '↓' if m.get('direction') == 'lower' else '?'})"
            for m in ms)))
    if ec.get("slices"):
        out.append(bullet(S["slices"], ", ".join(ec["slices"])))
    if ec.get("horizon"):
        out.append(bullet(S["horizon"], ec["horizon"]))
    sc = ec.get("scorer") or {}
    if sc.get("kind") or sc.get("details"):
        out.append(bullet(S["scorer"], " ".join(x for x in (sc.get("kind"), sc.get("details")) if x)))
    un = ec.get("uncertainty") or {}
    if un.get("method") or un.get("note"):
        out.append(bullet(S["uncertainty"], "; ".join(
            x for x in (un.get("method"),
                        f"level={un['level']}" if un.get("level") else None,
                        un.get("note")) if x)))
    pv = ec.get("provenance") or {}
    if any(pv.get(k) for k in ("eval_set_version", "labeled_by", "date")):
        out.append(bullet(S["provenance"], "; ".join(
            f"{k}={pv[k]}" for k in ("eval_set_version", "labeled_by", "date") if pv.get(k))))
    return out


def render_md(d, lang=None):
    """Template: 1 Tổng quan · 2 Nội dung chi tiết (bảng metric + lỗi theo nhóm) · 3 Kết luận. Markdown only."""
    lang = d.get("lang") or lang or "vi"
    S = STR.get(lang, STR["vi"])
    o, c = d["overview"], d["conclusion"]
    v = d.get("version", {})
    ec = d.get("eval_contract") or {}
    contract_metrics = {m["name"]: m.get("direction") for m in (ec.get("metrics") or []) if m.get("name")}
    md = [f"# Báo cáo: {d['title']}\n",
          f"Model `{v.get('model', '?')}` · Dataset `{v.get('dataset', '?')}` · Version trước `{d.get('baseline_name', 'N/A')}`"
          + (f" · {d['round']}" if d.get("round") else "") + "\n",
          f"## {S['sec1']}\n",
          bullet(S["status"], o["status"]),
          bullet(S["method"], o["method"]),
          bullet(S["result"], o["result"])]
    cl = _contract_lines(ec, S)
    if cl:
        md.append(f"\n### {S['contract']}\n\n" + "".join(cl))
    md.append(f"\n## {S['sec2']}\n")
    md.append(f"### {S['table_title']}\n{S['table_intro']}\n\n"
              f"| {S['c_scope']} | {S['c_item']} | {S['c_metric']} | {S['c_n']} | {S['c_prev']} | {S['c_cur']} | {S['c_delta']} |\n"
              "|---|---|---|---|---|---|---|\n")
    for tb in d["tables"]:
        for r in tb["rows"]:
            metric = _metric_label(r, tb, contract_metrics)
            unit = r.get("unit")
            n = r.get("n") or r.get("total") or tb.get("n") or "N/A"
            cur, cur_f = _score(r, raw=bool(unit))
            prv, prv_f = _score(r, "prev_", raw=bool(unit))
            same = (not r.get("prev_metric") or r.get("prev_metric") == (r.get("metric") or tb.get("metric") or metric.split(" ")[0])) and \
                   (not r.get("prev_set") or r.get("prev_set") == tb.get("eval_set", r.get("prev_set")))
            if cur is None or prv is None:
                delta = "N/A"
            elif not same:
                delta = f"{S['nodirect']} ({S['c_prev']}: {r.get('prev_metric') or ''} {r.get('prev_set') or ''})".strip()
            elif unit:
                delta = f"{cur - prv:+.2f} {unit}"
            else:
                delta = f"{cur - prv:+.1f} điểm" if lang == "vi" else f"{cur - prv:+.1f} pts"
            md.append(f"| {md_cell(tb['name'])} | {md_cell(r['item'])} | {md_cell(metric)} | {md_cell(n)} | "
                      f"{md_cell(_fmt(prv, prv_f, None, unit))} | "
                      f"{md_cell(_fmt(cur, cur_f, r.get('ci'), unit))} | {md_cell(delta)} |\n")
    md.append(f"\n### {S['err_title']}\n\n")
    if d["errors"]:
        md.append(f"| {S['c_cluster']} | {S['c_rate']} | {S['c_fields']} | {S['c_cause']} | {S['c_ex']} |\n|---|---|---|---|---|\n")
        for e in d["errors"]:
            den = e.get("denominator")
            rate = f"{e['count']}/{den} = {100 * e['count'] / den:.1f}%" if _num(den) else f"{e['count']} ({S['nosamples']})"
            first = (e.get("examples") or [None])[0]
            ex = e.get("example") or (first["where"] + (" — " + first["note"] if first.get("note") else "") if first else "")
            md.append(f"| {md_cell(e['cluster'])} | {md_cell(rate)} | {md_cell(e.get('fields', 'N/A'))} | "
                      f"{md_cell(e.get('cause', 'N/A'))} | {md_cell(ex)} |\n")
    else:
        md.append(f"Phân bố nhóm lỗi (0): {EMPTY_ERRORS_MSG[lang if lang in EMPTY_ERRORS_MSG else 'vi']}.\n")
    md.append(f"\n## {S['sec3']}\n\n{S['fixes_intro']}\n\n")
    for i, f in enumerate(c["fixes"], 1):
        md.append(f"{i}. **{f['priority']}** → {f['error']} → {f['fix']} → {f['measure']}\n")
    if c.get("recommend"):
        md.append("\n" + bullet(S["recommend"], c["recommend"]))
    if d.get("artifacts"):
        md.append("\n" + S["artifacts"] + ": " + "; ".join(f"[{a['label']}]({a['path']})" for a in d["artifacts"]) + "\n")
    return "".join(md)


def render_html(d, lang=None):
    lang = d.get("lang") or lang or "vi"
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
        dist = f'<h2>Phân bố nhóm lỗi (0)</h2><p>{e_(EMPTY_ERRORS_MSG[lang if lang in EMPTY_ERRORS_MSG else "vi"])}</p>'
        detail = ""
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
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
    ap.add_argument("--lang", choices=["vi", "en"], default=None,
                    help="Ghi đè ngôn ngữ (mặc định đọc eval.json `lang`, rồi vi).")
    a = ap.parse_args()
    with open(a.eval_json, encoding="utf-8") as f:
        d = json.load(f)
    os.makedirs(a.out_dir, exist_ok=True)
    for name, content in (("report.md", render_md(d, a.lang)), ("report.html", render_html(d, a.lang))):
        p = os.path.join(a.out_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        print("wrote", p)


if __name__ == "__main__":
    main()
