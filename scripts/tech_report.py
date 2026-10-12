#!/usr/bin/env python3
"""Technical report cho ca du an: bai bao khoa hoc tieng Viet viet sau khi ket thuc nghien cuu & phat trien.

  tech_report.py init <run_dir> [--set KEY=VALUE ...] [--force] [--out FILE]
      Sao chep templates/technical_report.template.md thanh <run_dir>/reports/technical_report.md,
      dien san cac khoa chung (TITLE, PROJECT, RUN_ID, DATE, ...) tu --set; khoa con lai de nguyen
      {{KEY}} cho agent dien bang so lieu da do. Khong ghi de file da co (tru --force).
  tech_report.py check <run_dir> [--file FILE]
      Exit 1 neu: thieu file, con {{KEY}} / <!-- HINT, thieu muc bat buoc, bang doi chieu khang dinh
      (Phu luc D) rong hoac trang thai sai dinh dang, so lieu o Tom tat khong xuat hien trong than bai,
      hoac round trong reports/ thieu report.md / report.html.
Phuong phap viet: skills/ai-pipeline-report/paper_writing_guide.md.
"""
import argparse
import datetime as dt
import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "templates", "technical_report.template.md")
PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+\}\}")
HINT = re.compile(r"<!--\s*HINT", re.I)
REQUIRED = [
    ("Tóm tắt", r"^##\s+Tóm tắt"),
    ("1. Giới thiệu", r"^##\s+1\.\s+Giới thiệu"),
    ("2. Nghiên cứu liên quan", r"^##\s+2\.\s+Nghiên cứu liên quan"),
    ("3. Phương pháp", r"^##\s+3\.\s+Phương pháp"),
    ("4. Thiết lập thực nghiệm", r"^##\s+4\.\s+Thiết lập thực nghiệm"),
    ("5. Kết quả", r"^##\s+5\.\s+Kết quả"),
    ("6. Thảo luận", r"^##\s+6\.\s+Thảo luận"),
    ("Hạn chế", r"\*\*Hạn chế"),
    ("7. Kết luận", r"^##\s+7\.\s+Kết luận"),
    ("8. Thực nghiệm bổ sung (kết quả âm)", r"^##\s+8\.\s+Thực nghiệm bổ sung"),
    ("Tài liệu tham khảo", r"^##\s+Tài liệu tham khảo"),
    ("Phụ lục D (đối chiếu khẳng định)", r"^###\s+D\.\s+Đối chiếu"),
]
STATUS_OK = re.compile(r"^(Đã có bằng chứng|Cần bằng chứng)", re.I)
NUMBER = re.compile(r"\d+[.,]\d+")


def report_path(run_dir, out=None):
    return out or os.path.join(run_dir, "reports", "technical_report.md")


def init(run_dir, values, force=False, out=None):
    dst = report_path(run_dir, out)
    if os.path.exists(dst) and not force:
        return dst, False
    values = {"DATE": dt.date.today().isoformat(), "RUN_ID": os.path.basename(os.path.abspath(run_dir)), **values}
    text = open(TEMPLATE, encoding="utf-8").read()
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, "w", encoding="utf-8", newline="\n").write(text)
    return dst, True


def _split(text, pattern):
    m = re.search(pattern, text, re.M)
    return (text[:m.start()], text[m.start():]) if m else (text, "")


def check(run_dir, file=None):
    """Danh sach van de; rong = dat."""
    path = file or report_path(run_dir)
    if not os.path.isfile(path):
        return [f"thieu {os.path.relpath(path, run_dir) if run_dir else path} (chay tech_report.py init)"]
    text = open(path, encoding="utf-8").read()
    problems = []
    left = sorted(set(PLACEHOLDER.findall(text)))
    if left:
        problems.append(f"con {len(left)} placeholder chua dien ({', '.join(left[:5])}{'...' if len(left) > 5 else ''})")
    n_hint = len(HINT.findall(text))
    if n_hint:
        problems.append(f"con {n_hint} khoi <!-- HINT chua xoa")
    for name, pat in REQUIRED:
        if not re.search(pat, text, re.M):
            problems.append(f"thieu muc bat buoc: {name}")
    # Phu luc D: moi dong khang dinh phai co bang chung + trang thai hop le
    _, appx = _split(text, r"^###\s+D\.\s+Đối chiếu")
    rows = [r for r in appx.splitlines() if r.startswith("|") and not re.match(r"^\|\s*[-:| ]+\|?\s*$", r)][1:]
    if appx and not rows:
        problems.append("Phu luc D khong co dong khang dinh nao")
    for r in rows:
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        if len(cells) < 3 or not all(cells[:3]):
            problems.append(f"Phu luc D: dong thieu o: {r[:60]}")
        elif not STATUS_OK.match(cells[2]):
            problems.append(f"Phu luc D: trang thai khong hop le ('Đã có bằng chứng...' hoac 'Cần bằng chứng: ...'): {cells[2][:50]}")
    # so lieu o Tom tat phai co trong than bai (khang dinh duoc ho tro bang chung)
    head, rest = _split(text, r"^##\s+Tóm tắt")
    abstract, body = _split(rest, r"^\*\*Từ khóa") if rest else ("", "")
    if abstract:
        _, after = _split(text, r"^##\s+1\.\s+Giới thiệu")
        norm = lambda s: s.replace(",", ".")
        body_nums = {norm(x) for x in NUMBER.findall(after)}
        missing = sorted({norm(x) for x in NUMBER.findall(abstract)} - body_nums)
        if missing:
            problems.append(f"so lieu o Tom tat khong xuat hien trong than bai: {', '.join(missing[:6])}")
    # moi round thi nghiem phai co report.md + report.html (nguon cua bai bao)
    for d in sorted(glob.glob(os.path.join(run_dir, "reports", "round-*"))):
        if os.path.isdir(d):
            for f in ("report.md", "report.html"):
                if not os.path.isfile(os.path.join(d, f)):
                    problems.append(f"{os.path.basename(d)}: thieu {f}")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("init")
    i.add_argument("run_dir")
    i.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    i.add_argument("--force", action="store_true", help="ghi de file da ton tai")
    i.add_argument("--out", help="duong dan file dich (mac dinh <run_dir>/reports/technical_report.md)")
    c = sp.add_parser("check")
    c.add_argument("run_dir")
    c.add_argument("--file", help="file bao cao can kiem (mac dinh <run_dir>/reports/technical_report.md)")
    a = ap.parse_args()
    if a.cmd == "init":
        vals = {}
        for kv in a.set:
            if "=" not in kv:
                sys.exit(f"--set can KEY=VALUE, nhan '{kv}'")
            k, v = kv.split("=", 1)
            vals[k.strip().upper()] = v
        dst, wrote = init(a.run_dir, vals, a.force, a.out)
        print(("da tao: " if wrote else "giu nguyen (da ton tai, dung --force de ghi de): ") + dst)
        return 0
    problems = check(a.run_dir, a.file)
    for p in problems:
        print("THIEU:", p)
    print("OK: technical report dat" if not problems else f"{len(problems)} van de")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
