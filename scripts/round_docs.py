#!/usr/bin/env python3
"""Ba tai lieu luu tru sau moi round huan luyen thu nghiem: thong ke du lieu, phuong phap, ket qua.

  round_docs.py init <round_dir> [--set KEY=VALUE ...] [--force]
      Sao chep templates/round_{data,method,results}_report.template.html thanh
      data_report.html / method_report.html / results_report.html trong <round_dir>,
      dien san cac khoa chung (ROUND_TITLE, PROJECT, VERSION, DATE, ...) tu --set; khoa con lai
      de nguyen dang {{KEY}} cho agent dien bang so lieu da do.
  round_docs.py check <round_dir>
      Exit 1 neu thieu 1 trong 3 file hoac con placeholder {{...}} chua dien.
"""
import argparse
import datetime as dt
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = {"data_report.html": "round_data_report.template.html",
        "method_report.html": "round_method_report.template.html",
        "results_report.html": "round_results_report.template.html"}
PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def init(round_dir, values, force=False):
    values = {"DATE": dt.date.today().isoformat(), **values}
    os.makedirs(round_dir, exist_ok=True)
    written, skipped = [], []
    for out, tpl in DOCS.items():
        dst = os.path.join(round_dir, out)
        if os.path.exists(dst) and not force:
            skipped.append(out)  # khong ghi de file da dien
            continue
        text = open(os.path.join(ROOT, "templates", tpl), encoding="utf-8").read()
        for k, v in values.items():
            text = text.replace("{{" + k + "}}", v)
        open(dst, "w", encoding="utf-8", newline="\n").write(text)
        written.append(out)
    return written, skipped


def check(round_dir):
    """Danh sach van de; rong = dat."""
    problems = []
    for out in DOCS:
        p = os.path.join(round_dir, out)
        if not os.path.isfile(p):
            problems.append(f"thieu {out}")
            continue
        left = sorted(set(PLACEHOLDER.findall(open(p, encoding="utf-8").read())))
        if left:
            problems.append(f"{out}: con {len(left)} placeholder chua dien ({', '.join(left[:5])}{'...' if len(left) > 5 else ''})")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("init")
    i.add_argument("round_dir")
    i.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    i.add_argument("--force", action="store_true", help="ghi de file da ton tai")
    c = sp.add_parser("check")
    c.add_argument("round_dir")
    a = ap.parse_args()
    if a.cmd == "init":
        vals = {}
        for kv in a.set:
            if "=" not in kv:
                sys.exit(f"--set can KEY=VALUE, nhan '{kv}'")
            k, v = kv.split("=", 1)
            vals[k.strip().upper()] = v
        written, skipped = init(a.round_dir, vals, a.force)
        print("da tao:", ", ".join(written) or "-", "| giu nguyen (da ton tai):", ", ".join(skipped) or "-")
        return 0
    problems = check(a.round_dir)
    for p in problems:
        print("THIEU:", p)
    print("OK: du 3 tai lieu round" if not problems else f"{len(problems)} van de")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
