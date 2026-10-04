#!/usr/bin/env python3
"""Validate an AI-system spec file and list the questions still open (Gate G1).

Spec format: markdown with `## <section>` headings (see templates/spec.template.md).
Exit code 0 = spec complete, 1 = questions remain, 2 = file error.
Use --json for machine-readable output (consumed by the coordinator).
"""
import argparse
import json
import re
import sys

# key -> (heading regexes, question to ask the human if missing/empty)
REQUIRED = {
    "goal": (r"muc tieu|mục tiêu|goal|objective|bai toan|bài toán",
             "Bài toán / mục tiêu nghiệp vụ là gì?"),
    "platform": (r"nen tang|nền tảng|deploy|trien khai|triển khai|platform",
                 "Triển khai trên GPU, CPU hay thiết bị biên (mobile)? Cấu hình tài nguyên?"),
    "input": (r"^input|dau vao|đầu vào",
              "Input gồm những gì? Cần bổ sung thành phần nào để làm rõ output?"),
    "output": (r"^output|dau ra|đầu ra",
               "Output khách cần là gì? Có tác nhân bên ngoài làm thay đổi output không?"),
    "dataset": (r"dataset|du lieu|dữ liệu|data",
                "Có data real không (số lượng, đã gán nhãn chưa)? Nếu không: có vài mẫu hoặc mô tả chi tiết được không?"),
    "targets": (r"chi tieu|chỉ tiêu|metric|target|kpi|muc tieu do luong|accuracy",
                "Chỉ tiêu mong muốn: độ chính xác, tốc độ, chi phí?"),
}
PLACEHOLDER = re.compile(r"(TODO|TBD|\?\?\?|<[^>]+>|\[điền|\[fill)", re.I)
OPTIONAL = re.compile(r"tuy chon|tùy chọn|optional", re.I)  # mục bổ sung (eval contract, split...) không thay mục bắt buộc


sys.stdout.reconfigure(encoding="utf-8")  # Windows pipes default to cp1252
sys.stderr.reconfigure(encoding="utf-8")


def split_sections(text):
    """Split on level-2 (`## `) headings only; `### ` and deeper stay in the body."""
    sections, cur, buf = [], None, []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*)", line)
        if m:
            if cur is not None:
                sections.append((cur, "\n".join(buf).strip()))
            cur, buf = m.group(1).strip(), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        sections.append((cur, "\n".join(buf).strip()))
    return sections


def find_all(sections, pattern):
    return [(title, body) for title, body in sections
            if re.search(pattern, title.lower()) and not OPTIONAL.search(title)]


def validate(text):
    sections = split_sections(text)
    missing, found = [], []
    for key, (pat, question) in REQUIRED.items():
        matches = find_all(sections, pat)
        if len(matches) > 1:
            titles = ", ".join(f"'{t}'" for t, _ in matches)
            missing.append({"key": key,
                            "question": f"Heading bắt buộc bị lặp ({titles}) — cần gộp thành một mục duy nhất."})
            continue
        title, body = matches[0] if matches else (None, None)
        body_clean = PLACEHOLDER.sub("", body or "").strip()
        if not title or len(body_clean) < 10:
            missing.append({"key": key, "question": question})
        else:
            found.append(key)
    return {"ok": not missing, "found": found, "questions": missing}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("spec")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        with open(a.spec, encoding="utf-8") as f:
            res = validate(f.read())
    except OSError as e:
        print(f"cannot read spec: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif res["ok"]:
        print("OK: spec đủ thông tin để sang phase Analysis.")
    else:
        print("Spec còn thiếu — câu hỏi cho Gate G1:")
        for q in res["questions"]:
            print(f"  - [{q['key']}] {q['question']}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
