"""Build the synthetic ds-v1.1 label overlay and corrected date crops."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

from PIL import Image


VERSION = "ds-v1.1"
SPLITS = ("train", "val", "test")
DATE_RE = re.compile(r"^\d{2}/\d{1,2}/\d{1,2}$")
FIELD_NAMES = ("date", "work_code", "start_time", "end_time", "break_time")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha256(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def load_labels(path: Path) -> dict[str, str]:
    labels = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, label = line.split("\t", 1)
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or name in labels:
            raise ValueError(f"Invalid or repeated label path: {name}")
        labels[name] = label
    return labels


def break_minutes(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    if value.endswith("m"):
        minutes = Decimal(value[:-1])
    elif value.endswith("h"):
        minutes = Decimal(value[:-1]) * 60
    elif ":" in value:
        hours, minutes_part = value.split(":", 1)
        if not hours.isdigit() or not minutes_part.isdigit() or int(minutes_part) >= 60:
            raise ValueError(f"Invalid break duration: {value}")
        minutes = Decimal(hours) * 60 + Decimal(minutes_part)
    else:
        minutes = Decimal(value)
    if minutes != int(minutes) or minutes < 0:
        raise ValueError(f"Invalid break duration: {value}")
    return int(minutes)


def split_pages(split: dict, source_pages: set[str]) -> dict[str, str]:
    if split["dataset_version"] != "ds-v1" or split["unit"] != "page":
        raise ValueError("Expected page-level split-v1 on ds-v1")
    page_to_split = {}
    for name in SPLITS:
        for page in split["splits"][name]:
            if page in page_to_split:
                raise ValueError(f"Page in multiple splits: {page}")
            page_to_split[page] = name
    if set(page_to_split) != source_pages:
        raise ValueError("Split pages do not match source pages")
    folds = split["cv7_folds"]
    if set().union(*(set(pages) for pages in folds.values())) != source_pages:
        raise ValueError("CV folds do not cover source pages")
    if sum(map(len, folds.values())) != len(source_pages):
        raise ValueError("CV folds overlap")
    return page_to_split


def date_crop_box(layout: dict, row: int, image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    dates = [item for item in layout["boxes"] if item["cls"] == "date" and item["row"] == row]
    if len(dates) != 1:
        raise ValueError(f"Expected one date bbox for row {row}; got {len(dates)}")
    x1, y1, x2, y2 = dates[0]["bbox_xyxy"]
    neighbors = [item["bbox_xyxy"][0] for item in layout["boxes"]
                 if item["cls"] == "work_code" and item["row"] == row]
    if len(neighbors) > 1 or (neighbors and neighbors[0] <= x2):
        raise ValueError(f"No clean date/work_code boundary in row {row}")
    right_pad = min(20, max(0, math.floor((neighbors[0] - x2) / 2))) if neighbors else 20
    box = (max(0, math.floor(x1) - 8), max(0, math.floor(y1) - 8),
           min(image_size[0], math.ceil(x2) + right_pad),
           min(image_size[1], math.ceil(y2) + 8))
    if box[2] <= box[0] or box[3] <= box[1] or (neighbors and box[2] >= neighbors[0]):
        raise ValueError(f"Invalid date crop box in row {row}: {box}")
    return box


def label_lines(labels: dict[str, str], prefix: str, page_to_split: dict[str, str]) -> dict[str, list[str]]:
    result = {name: [] for name in SPLITS}
    for relative, label in sorted(labels.items()):
        page = Path(relative).parts[1]
        result[page_to_split[page]].append(f"{prefix}/{relative}\t{label}\n")
    return result


def build(source: Path, split_path: Path, output: Path, summary_dir: Path | None) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output must be empty: {output}")
    source_pages = {p.stem for p in (source / "kie").glob("*.json")}
    split = read_json(split_path)
    page_to_split = split_pages(split, source_pages)
    rec_labels = load_labels(source / "rec/hw_label.txt")
    print_labels = load_labels(source / "rec/print_label.txt")
    old_whole_labels = {name: label for name, label in
                        load_labels(source / "rec_date_whole_cell/hw_label.txt").items()
                        if ".date_" in name}
    expected_hw = {}
    expected_whole = {}
    rows = []
    boxes = {}
    break_counts = Counter()
    date_parts = Counter()
    page_metadata = {}
    for page in sorted(source_pages):
        kie = read_json(source / "kie" / f"{page}.json")
        layout = read_json(source / "layout/boxes" / f"{page}.json")
        if kie["page_id"] != page or not kie["synthetic"]:
            raise ValueError(f"Invalid page metadata: {page}")
        page_metadata[page] = {"profile": kie["profile"], "degrade": kie["degrade"]}
        visible_records = kie["visible"]["records"]
        if len(visible_records) != len(kie["gt"]["records"]):
            raise ValueError(f"Record count mismatch: {page}")
        for row_index, (visible, gt) in enumerate(zip(visible_records, kie["gt"]["records"])):
            date = visible["date"]
            parts = None
            if date:
                if not DATE_RE.fullmatch(date):
                    raise ValueError(f"Unexpected visible date: {page}/{row_index}: {date}")
                parts = date.split("/")
                for index, part in enumerate(parts):
                    relative = f"hw/{page}/records.{row_index}.date_{row_index}_p{index}.jpg"
                    expected_hw[relative] = part
                    date_parts[f"p{index}"] += 1
                whole = f"hw/{page}/records.{row_index}.date_{row_index}.jpg"
                expected_whole[whole] = date
                boxes[whole] = (page, row_index)
            for field in FIELD_NAMES[1:]:
                if visible[field]:
                    relative = f"hw/{page}/records.{row_index}.{field}_{row_index}.jpg"
                    expected_hw[relative] = visible[field]
            minutes = break_minutes(gt["break_time"])
            if minutes != break_minutes(visible["break_time"]):
                raise ValueError(f"Visible/GT break mismatch: {page}/{row_index}")
            break_counts[str(minutes)] += 1
            rows.append({"dataset_version": VERSION, "page_id": page, "row": row_index,
                         "split": page_to_split[page], "date_visible": date,
                         "date_parts": parts, "break_min": minutes})
    if rec_labels != expected_hw:
        mismatches = [(key, rec_labels.get(key), expected_hw.get(key)) for key in set(rec_labels) | set(expected_hw)
                      if rec_labels.get(key) != expected_hw.get(key)]
        raise ValueError(f"ds-v1 HW labels disagree with visible fields: {mismatches[:5]}")
    if set(old_whole_labels) != set(expected_whole):
        raise ValueError("Old whole-cell crop set differs from visible date rows")

    output.mkdir(parents=True, exist_ok=True)
    for relative in sorted(set(rec_labels) | set(print_labels)):
        src = source / "rec" / relative
        dst = output / "rec" / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    for page in sorted(source_pages):
        page_boxes = [(relative, row) for relative, (p, row) in boxes.items() if p == page]
        if not page_boxes:
            continue
        layout = read_json(source / "layout/boxes" / f"{page}.json")
        with Image.open(source / "images" / f"{page}.jpg") as image:
            for relative, row in page_boxes:
                box = date_crop_box(layout, row, image.size)
                dst = output / "date_whole_cell" / relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                image.crop(box).convert("RGB").save(dst, format="JPEG", quality=95, subsampling=0)

    label_dir = output / "labels"
    label_dir.mkdir()
    datasets = (("hw", rec_labels, "rec"), ("print", print_labels, "rec"),
                ("whole_date", expected_whole, "date_whole_cell"))
    for category, labels, prefix in datasets:
        for split_name, lines in label_lines(labels, prefix, page_to_split).items():
            (label_dir / f"{split_name}_{category}.txt").write_text("".join(lines), encoding="utf-8")
    for split_name in SPLITS:
        (label_dir / f"{split_name}_pages.txt").write_text(
            "".join(f"{page}\n" for page in sorted(split["splits"][split_name])), encoding="utf-8")
    (output / "rows.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    data_files = [path for path in output.rglob("*") if path.is_file()]
    split_counts = {name: {"pages": sum(s == name for s in page_to_split.values()),
                           "rows": sum(row["split"] == name for row in rows),
                           "hw_crops": len(label_lines(rec_labels, "rec", page_to_split)[name]),
                           "print_crops": len(label_lines(print_labels, "rec", page_to_split)[name]),
                           "whole_date_crops": len(label_lines(expected_whole, "date_whole_cell", page_to_split)[name]),
                           "degrade": dict(sorted(Counter(page_metadata[page]["degrade"] for page in split["splits"][name]).items())),
                           "profile": dict(sorted(Counter(page_metadata[page]["profile"] for page in split["splits"][name]).items()))}
                    for name in SPLITS}
    manifest = {"dataset_version": VERSION, "parent_dataset_version": "ds-v1",
                "split_version": "split-v1", "synthetic_only": True,
                "source": str(source.resolve()), "source_manifest_sha256": sha256(source / "manifest.json"),
                "source_kie_sha256": tree_sha256(source, list((source / "kie").glob("*.json"))),
                "split_sha256": sha256(split_path), "files": len(data_files),
                "content_sha256": tree_sha256(output, data_files),
                "counts": {"pages": len(source_pages), "rows": len(rows),
                           "date_parts": dict(date_parts), "whole_date_crops": len(expected_whole),
                           "break_min": dict(sorted(break_counts.items())), "splits": split_counts},
                "crop_rule": "date bbox padded left/top/bottom 8px, right <=20px and <half gap to work_code",
                "training_labels": "labels/train_*.txt only; val/test excluded from training and vocab"}
    write_json(output / "manifest.json", manifest)
    report = make_report(manifest, old_whole_labels, expected_whole)
    (output / "data_report.md").write_text(report, encoding="utf-8")
    if summary_dir:
        summary_dir.mkdir(parents=True, exist_ok=True)
        write_json(summary_dir / "manifest.ds-v1.1.json", manifest)
        (summary_dir / "data_report.md").write_text(report, encoding="utf-8")
    return manifest


def make_report(manifest: dict, old_whole: dict[str, str], new_whole: dict[str, str]) -> str:
    counts = manifest["counts"]
    split_lines = [f"| {name} | {values['pages']} | {values['rows']} | {values['hw_crops']} | "
                   f"{values['print_crops']} | {values['whole_date_crops']} |"
                   for name, values in counts["splits"].items()]
    changed = sum(old_whole[name] != new_whole[name] for name in new_whole)
    return "\n".join([
        "# Báo cáo dữ liệu ds-v1.1", "",
        "**Synthetic-only, không đảm bảo production.** Nguồn ds-v1 (100-sync-hw-v1), split-v1 theo trang.", "",
        "## Tổng quan", "",
        f"{counts['pages']} trang, {counts['rows']} dòng. "
        f"Có {counts['whole_date_crops']} ngày nhìn thấy và crop whole-cell đã tạo lại.", "",
        "## Nội dung chi tiết", "",
        "| Split | Trang | Dòng | HW crop | Print crop | Whole-date crop |",
        "|---|---:|---:|---:|---:|---:|", *split_lines, "",
        f"Nhãn date p0/p1/p2 từ visible.date: {counts['date_parts']}. "
        f"Nhãn whole-cell cũ khác chuỗi có dấu phân cách ở {changed}/{len(new_whole)} crop. "
        "Crop mới dùng bbox date và chặn trước bbox work_code, bỏ ô dư ở mép phải.", "",
        f"Phân bố break_min (phút, null giữ nguyên): {counts['break_min']}.", "",
        "Phân bố suy hao ảnh theo trang: " + "; ".join(
            f"{name}={values['degrade']}" for name, values in counts["splits"].items()) + ".", "",
        "Phân bố 14 profile theo trang: " + "; ".join(
            f"{name}={values['profile']}" for name, values in counts["splits"].items()) + ".", "",
        "Train/val/test có file nhãn riêng; tập train chỉ tham chiếu trang train. "
        "Script kiểm tra trang không trùng giữa các split và fold, nhãn HW khớp visible, "
        "break từ visible khớp GT sau chuẩn hóa. Không tạo từ vựng từ val/test.", "",
        f"SHA-256 nội dung ds-v1.1: `{manifest['content_sha256']}`. "
        f"SHA-256 split-v1: `{manifest['split_sha256']}`.", "",
        "## Kết luận", "",
        "Bộ ds-v1.1 tái lập được từ ds-v1 + split-v1; các số đếm và hash chỉ xác nhận "
        "tính nhất quán của dữ liệu synthetic, không đo chất lượng OCR trên ảnh thật.", ""])


def verify(output: Path) -> dict:
    manifest = read_json(output / "manifest.json")
    if manifest["dataset_version"] != VERSION:
        raise ValueError("Wrong dataset version")
    data_files = [path for path in output.rglob("*") if path.is_file()
                  and path.name not in {"manifest.json", "data_report.md"}]
    if len(data_files) != manifest["files"] or tree_sha256(output, data_files) != manifest["content_sha256"]:
        raise ValueError("Dataset hash mismatch")
    pages = {name: set((output / "labels" / f"{name}_pages.txt").read_text(encoding="utf-8").splitlines())
             for name in SPLITS}
    if any(pages[a] & pages[b] for a in SPLITS for b in SPLITS if a != b):
        raise ValueError("Split page overlap")
    for name in SPLITS:
        for category in ("hw", "print", "whole_date"):
            for line in (output / "labels" / f"{name}_{category}.txt").read_text(encoding="utf-8").splitlines():
                relative, _ = line.split("\t", 1)
                if Path(relative).parts[2] not in pages[name] or not (output / relative).is_file():
                    raise ValueError(f"Cross-split or missing crop: {relative}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--split", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary-dir", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        if args.source is None or args.split is None:
            parser.error("build requires --source and --split")
        manifest = build(args.source, args.split, args.output, args.summary_dir)
    else:
        manifest = verify(args.output)
    print(f"{VERSION}: {manifest['content_sha256']} ({manifest['files']} files)")


if __name__ == "__main__":
    main()
