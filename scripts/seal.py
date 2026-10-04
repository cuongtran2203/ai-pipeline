#!/usr/bin/env python3
"""Seal nhan test toi thieu (minimal test-label seal).

Sua loi P1 "test seal dua vao ky luat nguoi chay" trong
runs/ai-pipeline-v2/reviews/debate_v2_improvements.md va lam theo muc
"Seal nhan test toi thieu cho wave sau" cua runs/ai-pipeline-v2/reviews/review_wave_fix_p1.md.

Khong ma hoa/KMS rieng: manifest chi ghi hash (khong chua nhan), va moi lan mo seal
duoc ghi append-only vao seal_audit.jsonl qua scripts/statefile.append_jsonl.

Lenh:
    manifest <run_dir> --dataset-version V --split-id S --ids-file F --labels-file L [--out ...]
    verify   <run_dir>
    lock     <run_dir> --model-version V --threshold-file F
    grant    <run_dir> --role R --task T --purpose P [--actor A] [--dispatch D]

Quy tac grant: chi role integrator/evaluator, sau khi recipe da khoa, moi duoc tra
duong dan nhan test. Mo lan hai duoc danh dau exploratory (khong con la blind final).

Stdlib only.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import statefile  # noqa: E402

ROOT = os.path.dirname(HERE)
MANIFEST_NAME = "eval_manifest.json"
RECIPE_LOCK_NAME = "recipe_lock.json"
SEAL_AUDIT_NAME = "seal_audit.jsonl"
MANIFEST_SCHEMA = "eval_manifest/v1"
RECIPE_SCHEMA = "recipe_lock/v1"
ALLOWED_ROLES = ("integrator", "evaluator")
DISPATCH_ENV = "ORCA_DISPATCH_ID"


class SealError(RuntimeError):
    """Loi cau hinh/dau vao cua seal."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
def canonical_hash(obj):
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def to_ref(path):
    """Duong dan tuong doi tu goc du an (POSIX) neu nam trong root, nguoc lai tra tuyet doi."""
    absolute = os.path.abspath(path)
    try:
        rel = os.path.relpath(absolute, ROOT)
    except ValueError:  # khac o dia tren Windows
        return absolute.replace("\\", "/")
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return absolute.replace("\\", "/")
    return rel.replace("\\", "/")


def resolve_ref(ref):
    if os.path.isabs(ref):
        return os.path.normpath(ref)
    return os.path.normpath(os.path.join(ROOT, ref.replace("/", os.sep)))


def _actor(explicit, fallback):
    return explicit or os.environ.get("AI_PIPELINE_ACTOR") or fallback


def _count_lines(path):
    with open(path, encoding="utf-8-sig") as f:
        return sum(1 for line in f if line.strip())
def make_manifest(run_dir, dataset_version, split_id, ids_file, labels_file, out=None, actor=None):
    """Tao eval_manifest.json: chi hash + duong dan, KHONG chua nhan."""
    if not os.path.isfile(ids_file):
        raise SealError(f"khong thay ids-file: {ids_file}")
    if not os.path.isfile(labels_file):
        raise SealError(f"khong thay labels-file: {labels_file}")
    n_ids, n_labels = _count_lines(ids_file), _count_lines(labels_file)
    if n_ids != n_labels:
        raise SealError(f"so mau lech giua danh sach ID ({n_ids}) va file nhan ({n_labels})")
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "run_id": os.path.basename(os.path.abspath(run_dir)),
        "dataset_version": dataset_version,
        "split_id": split_id,
        "n_samples": n_ids,
        "ids_path": to_ref(ids_file),
        "ids_sha256": sha256_file(ids_file),
        "labels_path": to_ref(labels_file),
        "labels_sha256": sha256_file(labels_file),
        "sealed_at": utc_now(),
        "created_by": _actor(actor, "unknown"),
    }
    manifest["manifest_hash"] = canonical_hash(manifest)
    out_path = out or os.path.join(run_dir, MANIFEST_NAME)
    if not os.path.isabs(out_path):
        out_path = os.path.abspath(out_path)
    statefile.update_json(out_path, lambda _old: manifest, default=None)
    manifest["manifest_path"] = out_path
    return manifest

def verify(run_dir, manifest_path=None):
    """Kiem manifest_hash va hash cua danh sach ID/file nhan con khop khong."""
    path = manifest_path or os.path.join(run_dir, MANIFEST_NAME)
    manifest = statefile.read_json(path)
    if not manifest:
        return {"ok": False, "reason": "no_manifest", "manifest_path": path}
    core = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    if manifest.get("manifest_hash") != canonical_hash(core):
        return {"ok": False, "reason": "manifest_tampered", "manifest_path": path}
    ids_path, labels_path = resolve_ref(manifest["ids_path"]), resolve_ref(manifest["labels_path"])
    if not os.path.isfile(ids_path):
        return {"ok": False, "reason": "ids_missing", "manifest_path": path}
    if sha256_file(ids_path) != manifest["ids_sha256"]:
        return {"ok": False, "reason": "ids_hash_mismatch", "manifest_path": path}
    if not os.path.isfile(labels_path):
        return {"ok": False, "reason": "labels_missing", "manifest_path": path}
    if sha256_file(labels_path) != manifest["labels_sha256"]:
        return {"ok": False, "reason": "labels_hash_mismatch", "manifest_path": path}
    return {"ok": True, "reason": "ok", "manifest_path": path, "manifest": manifest, "labels_path": labels_path}

def lock_recipe(run_dir, model_version, threshold_file, actor=None):
    """Khoa recipe (model version + hash file nguong) truoc khi duoc mo seal."""
    lock_path = os.path.join(run_dir, RECIPE_LOCK_NAME)
    if os.path.exists(lock_path):
        raise SealError(f"recipe da khoa ({lock_path}); xoa tay neu that su can khoa lai")
    if not os.path.isfile(threshold_file):
        raise SealError(f"khong thay threshold-file: {threshold_file}")
    record = {
        "schema": RECIPE_SCHEMA,
        "model_version": model_version,
        "threshold_file": to_ref(threshold_file),
        "threshold_sha256": sha256_file(threshold_file),
        "locked_at": utc_now(),
        "locked_by": _actor(actor, "unknown"),
    }
    statefile.update_json(lock_path, lambda _old: record, default=None)
    record["lock_path"] = lock_path
    return record

def grant(run_dir, role, task, purpose, actor=None, dispatch=None):
    """Tra duong dan nhan test neu role hop le + recipe da khoa; ghi audit moi lan grant/deny."""
    audit_path = os.path.join(run_dir, SEAL_AUDIT_NAME)
    base = {
        "utc_time": utc_now(),
        "actor": _actor(actor, role),
        "role": role,
        "task": task,
        "dispatch": dispatch or os.environ.get(DISPATCH_ENV, ""),
        "purpose": purpose,
        "manifest_hash": None,
        "recipe_version": None,
        "exploratory": False,
    }

    def deny(reason):
        rec = dict(base, result="denied", reason=reason)
        statefile.append_jsonl(audit_path, rec)
        return {"granted": False, "labels_path": None, "reason": reason, "exploratory": False, "audit": rec}

    manifest = statefile.read_json(os.path.join(run_dir, MANIFEST_NAME))
    if not manifest:
        return deny("no_manifest")
    base["manifest_hash"] = manifest.get("manifest_hash")
    checked = verify(run_dir)
    if not checked["ok"]:
        return deny(checked["reason"])
    if role not in ALLOWED_ROLES:
        return deny("role_not_permitted")
    recipe = statefile.read_json(os.path.join(run_dir, RECIPE_LOCK_NAME))
    if not recipe:
        return deny("recipe_not_locked")
    base["recipe_version"] = recipe.get("model_version")
    labels_path = resolve_ref(manifest["labels_path"])
    if not os.path.isfile(labels_path):
        return deny("labels_missing")
    exploratory = any(r.get("result") == "granted" for r in statefile.read_jsonl(audit_path))
    rec = dict(base, result="granted", reason="ok", exploratory=exploratory)
    statefile.append_jsonl(audit_path, rec)
    return {"granted": True, "labels_path": labels_path, "reason": "ok", "exploratory": exploratory, "audit": rec}

def build_parser():
    ap = argparse.ArgumentParser(prog="seal.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest", help="tao eval_manifest.json (khong chua nhan)")
    m.add_argument("run_dir")
    m.add_argument("--dataset-version", required=True)
    m.add_argument("--split-id", required=True)
    m.add_argument("--ids-file", required=True)
    m.add_argument("--labels-file", required=True)
    m.add_argument("--out")
    m.add_argument("--actor")

    v = sub.add_parser("verify", help="kiem hash manifest/con nhan con khop")
    v.add_argument("run_dir")

    lk = sub.add_parser("lock", help="khoa recipe (model version + hash nguong)")
    lk.add_argument("run_dir")
    lk.add_argument("--model-version", required=True)
    lk.add_argument("--threshold-file", required=True)
    lk.add_argument("--actor")

    g = sub.add_parser("grant", help="xin duong dan nhan test theo role")
    g.add_argument("run_dir")
    g.add_argument("--role", required=True)
    g.add_argument("--task", required=True)
    g.add_argument("--purpose", required=True)
    g.add_argument("--actor")
    g.add_argument("--dispatch")
    return ap

def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "manifest":
            man = make_manifest(args.run_dir, args.dataset_version, args.split_id,
                                args.ids_file, args.labels_file, out=args.out, actor=args.actor)
            _print({k: man[k] for k in ("schema", "run_id", "dataset_version", "split_id", "n_samples",
                                        "ids_sha256", "labels_sha256", "labels_path", "manifest_hash",
                                        "manifest_path")})
            return 0
        if args.cmd == "verify":
            res = verify(args.run_dir)
            _print(res)
            return 0 if res["ok"] else 1
        if args.cmd == "lock":
            _print(lock_recipe(args.run_dir, args.model_version, args.threshold_file, actor=args.actor))
            return 0
        if args.cmd == "grant":
            res = grant(args.run_dir, args.role, args.task, args.purpose, actor=args.actor, dispatch=args.dispatch)
            if not res["granted"]:
                print(f"TU CHOI: {res['reason']}", file=sys.stderr)
                return 1
            print(res["labels_path"])
            if res["exploratory"]:
                print("CANH BAO: mo lai seal -> ket qua la exploratory, KHONG phai blind final", file=sys.stderr)
            return 0
    except SealError as e:
        print(f"LOI: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())