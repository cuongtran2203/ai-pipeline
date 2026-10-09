#!/usr/bin/env python3
"""Seal nhan test toi thieu (minimal test-label seal).

Sua lo hong P1 "seal nhan test" cua RV3/RV4 (runs/ai-pipeline-v2/reviews/review_wave3.md).
Khong ma hoa/KMS rieng: gioi han bang manifest hash + role (lay tu plan) + recipe hash
+ audit co chuoi hash (prev_hash/hash) + anchor. Xem scripts/seal.md de biet gioi han
(day KHONG phai bien bao ve o muc filesystem).

Lenh:
    manifest <run_dir> --dataset-version V --split-id S --ids-file F --labels-file L
             [--labels-dir D] [--out ...] [--actor A]
    verify   <run_dir>
    lock     <run_dir> --model-version V --threshold-file F [--actor A]
    grant    <run_dir> --task T --purpose P [--role R] [--actor A] [--dispatch D]
    verify-audit <run_dir>

Quy tac grant: role cua task lay TU PLAN (plan.json trong run_dir hoac artifacts/*/plan.json),
chi integrator/evaluator duoc cap; --role chi de kiem cheo (khac role trong plan -> tu choi);
sau khi recipe da khoa va threshold khong doi moi tra duong dan nhan test. Mo lan hai duoc
danh dau exploratory (khong con la blind final). Moi quyet dinh nam trong MOT khoa rieng
(seal_audit.guard) nen hai grant dong thoi chi co dung MOT blind-final.

Stdlib only.
"""
import argparse
import glob
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
AUDIT_ANCHOR_NAME = "seal_audit.anchor.json"
AUDIT_GUARD_NAME = "seal_audit.guard"
RECIPE_GUARD_NAME = "recipe_lock.guard"
MANIFEST_SCHEMA = "eval_manifest/v1"
RECIPE_SCHEMA = "recipe_lock/v1"
AUDIT_ANCHOR_SCHEMA = "seal_audit_anchor/v1"
GENESIS_HASH = "0" * 64
ALLOWED_ROLES = ("integrator", "evaluator")
DISPATCH_ENV = "HERDR_DISPATCH_ID"
LABELS_ROOT_ENV = "SEAL_LABELS_ROOT"
ENV_ROOT_TOKEN = "$SEAL_LABELS_ROOT"


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


def _relpath(path, base):
    try:
        return os.path.relpath(os.path.abspath(path), base)
    except ValueError:  # khac o dia tren Windows
        return None


def to_ref(path):
    """Ref tuong doi (POSIX) tinh tu goc du an; khac o dia -> None (khong ghi tuyet doi)."""
    rel = _relpath(path, ROOT)
    return None if rel is None else rel.replace("\\", "/")


def resolve_ref(ref, base=None):
    """Giai ma ref: tuyet doi giu nguyen, tuong doi tinh tu `base` (mac dinh goc du an)."""
    ref = str(ref).replace("\\", "/")
    if os.path.isabs(ref):
        return os.path.normpath(ref)
    return os.path.normpath(os.path.join(base or ROOT, ref.replace("/", os.sep)))


def _actor(explicit, fallback):
    return explicit or os.environ.get("AI_PIPELINE_ACTOR") or fallback


def _count_lines(path):
    with open(path, encoding="utf-8-sig") as f:
        return sum(1 for line in f if line.strip())


# --- Duong dan nhan: root ngoai checkout + khong ghi tuyet doi vao manifest ---

def _is_under(path, root):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:  # khac o dia
        return False


def _labels_root(explicit):
    """Tra (root tuyet doi, nguon) voi nguon in {arg, env, default}."""
    if explicit:
        return os.path.abspath(explicit), "arg"
    env = os.environ.get(LABELS_ROOT_ENV)
    if env:
        return os.path.abspath(env), "env"
    return os.path.abspath(ROOT), "default"


def resolve_labels_path(manifest):
    """Tra (duong dan nhan tuyet doi, loi). Ho tro manifest cu (labels_path tuyet doi)."""
    labels_ref = manifest.get("labels_path")
    if not labels_ref:
        return None, "labels_path_missing"
    if os.path.isabs(labels_ref):
        return os.path.normpath(labels_ref), None
    root_ref = manifest.get("labels_root", ".")
    if root_ref == ENV_ROOT_TOKEN:
        env = os.environ.get(LABELS_ROOT_ENV)
        if not env:
            return None, "labels_root_unset"
        root = os.path.abspath(env)
    elif root_ref in (None, "", "."):
        root = ROOT
    else:
        root = resolve_ref(root_ref)
    return os.path.normpath(os.path.join(root, labels_ref.replace("/", os.sep))), None


def make_manifest(run_dir, dataset_version, split_id, ids_file, labels_file, out=None, actor=None, labels_root=None):
    """Tao eval_manifest.json: chi hash + duong dan, KHONG chua nhan.

    labels_file phai nam trong labels_root (mac dinh = goc du an). Nhan dat NGOAI checkout
    thi truyen --labels-dir/SEAL_LABELS_ROOT; manifest chi luu duong dan tuong doi.
    """
    if not os.path.isfile(ids_file):
        raise SealError(f"khong thay ids-file: {ids_file}")
    if not os.path.isfile(labels_file):
        raise SealError(f"khong thay labels-file: {labels_file}")
    n_ids, n_labels = _count_lines(ids_file), _count_lines(labels_file)
    if n_ids != n_labels:
        raise SealError(f"so mau lech giua danh sach ID ({n_ids}) va file nhan ({n_labels})")
    ids_ref = to_ref(ids_file)
    if ids_ref is None:
        raise SealError("ids-file khac o dia voi goc du an; khong the ghi duong dan tuong doi")
    root, source = _labels_root(labels_root)
    if not _is_under(labels_file, root):
        raise SealError(
            f"labels-file khong nam trong labels-root ({root}); dung --labels-dir hoac SEAL_LABELS_ROOT")
    labels_rel = _relpath(labels_file, root).replace("\\", "/")
    if source == "env":
        root_ref = ENV_ROOT_TOKEN
    else:
        root_ref = to_ref(root)
        if root_ref is None:
            raise SealError("labels-root khac o dia; dat SEAL_LABELS_ROOT de tranh ghi duong dan tuyet doi")
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "run_id": os.path.basename(os.path.abspath(run_dir)),
        "dataset_version": dataset_version,
        "split_id": split_id,
        "n_samples": n_ids,
        "ids_path": ids_ref,
        "ids_sha256": sha256_file(ids_file),
        "labels_path": labels_rel,
        "labels_root": root_ref,
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
    ids_path = resolve_ref(manifest["ids_path"])
    labels_path, err = resolve_labels_path(manifest)
    if err:
        return {"ok": False, "reason": err, "manifest_path": path}
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
    """Khoa recipe (model version + hash file nguong). Kiem ton tai + ghi trong CUNG mot khoa."""
    lock_path = os.path.join(run_dir, RECIPE_LOCK_NAME)
    if not os.path.isfile(threshold_file):
        raise SealError(f"khong thay threshold-file: {threshold_file}")
    threshold_ref = to_ref(threshold_file)
    if threshold_ref is None:
        raise SealError("threshold-file khac o dia voi goc du an; khong the ghi duong dan tuong doi")
    record = {
        "schema": RECIPE_SCHEMA,
        "model_version": model_version,
        "threshold_file": threshold_ref,
        "threshold_sha256": sha256_file(threshold_file),
        "locked_at": utc_now(),
        "locked_by": _actor(actor, "unknown"),
    }
    with statefile.file_lock(os.path.join(run_dir, RECIPE_GUARD_NAME)):
        if os.path.exists(lock_path):
            raise SealError(f"recipe da khoa ({lock_path}); xoa tay neu that su can khoa lai")
        statefile.update_json(lock_path, lambda _old: record, default=None)
    record["lock_path"] = lock_path
    return record


# --- Audit co chuoi hash (prev_hash/hash) + anchor chong cat cuoi ---

def _audit_hash(rec):
    core = {k: v for k, v in rec.items() if k != "hash"}
    return canonical_hash(core)


def _read_audit_strict(path):
    """Doc JSONL nghiem ngat: dong giua khong parse duoc -> loi (khong am tham bo qua)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        return [], None
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            return [], "audit_unparseable"
    return records, None


def verify_audit(run_dir):
    """Kiem chuoi hash cua seal_audit.jsonl + anchor: phat hien xoa/sua/cat dong."""
    audit_path = os.path.join(run_dir, SEAL_AUDIT_NAME)
    anchor_path = os.path.join(run_dir, AUDIT_ANCHOR_NAME)
    records, err = _read_audit_strict(audit_path)
    if err:
        return {"ok": False, "reason": err, "count": 0}
    prev = GENESIS_HASH
    for idx, rec in enumerate(records):
        if not isinstance(rec, dict) or "hash" not in rec or "prev_hash" not in rec:
            return {"ok": False, "reason": "record_missing_hash", "index": idx, "count": len(records)}
        if rec["prev_hash"] != prev:
            return {"ok": False, "reason": "chain_broken", "index": idx, "count": len(records)}
        if _audit_hash(rec) != rec["hash"]:
            return {"ok": False, "reason": "record_tampered", "index": idx, "count": len(records)}
        prev = rec["hash"]
    anchor = statefile.read_json(anchor_path)
    if not records:
        return {"ok": True, "reason": "ok", "count": 0}
    if not anchor:
        return {"ok": False, "reason": "anchor_missing", "count": len(records)}
    if anchor.get("count") != len(records) or anchor.get("last_hash") != prev:
        return {"ok": False, "reason": "truncated", "count": len(records)}
    return {"ok": True, "reason": "ok", "count": len(records)}


def _write_anchor(run_dir, count, last_hash):
    path = os.path.join(run_dir, AUDIT_ANCHOR_NAME)
    record = {
        "schema": AUDIT_ANCHOR_SCHEMA,
        "count": count,
        "last_hash": last_hash,
        "updated_at": utc_now(),
    }
    statefile.update_json(path, lambda _old: record, default=None)


def _append_audit(run_dir, rec):
    """Gan prev_hash/hash theo chuoi hien tai roi append; cap nhat anchor. Goi trong guard lock."""
    audit_path = os.path.join(run_dir, SEAL_AUDIT_NAME)
    records, _err = _read_audit_strict(audit_path)
    prev = records[-1]["hash"] if records else GENESIS_HASH
    rec = dict(rec)
    rec.pop("hash", None)
    rec.pop("prev_hash", None)
    rec["prev_hash"] = prev
    rec["hash"] = _audit_hash(rec)
    statefile.append_jsonl(audit_path, rec)
    _write_anchor(run_dir, len(records) + 1, rec["hash"])
    return rec


# --- Plan: lay role cua task tu plan.json ---

def find_plan(run_dir):
    """Tim plan.json trong run_dir hoac artifacts/*/plan.json."""
    direct = os.path.join(run_dir, "plan.json")
    if os.path.isfile(direct):
        plan = statefile.read_json(direct)
        if plan:
            return plan
    for candidate in sorted(glob.glob(os.path.join(run_dir, "artifacts", "*", "plan.json"))):
        plan = statefile.read_json(candidate)
        if plan:
            return plan
    return None


def plan_task_role(run_dir, task):
    """Tra (role, loi): role cua task trong plan; loi in {no_plan, task_not_in_plan}."""
    plan = find_plan(run_dir)
    if plan is None:
        return None, "no_plan"
    for item in plan.get("tasks", []):
        if item.get("id") == task:
            return item.get("role"), None
    return None, "task_not_in_plan"


def grant(run_dir, role, task, purpose, actor=None, dispatch=None):
    """Cap duong dan nhan test neu role (tu plan) hop le + recipe khop; ghi audit moi lan."""
    audit_path = os.path.join(run_dir, SEAL_AUDIT_NAME)
    base = {
        "utc_time": utc_now(),
        "actor": _actor(actor, role or "unknown"),
        "role": role,
        "task": task,
        "dispatch": dispatch or os.environ.get(DISPATCH_ENV, ""),
        "purpose": purpose,
        "manifest_hash": None,
        "recipe_version": None,
        "exploratory": False,
    }
    with statefile.file_lock(os.path.join(run_dir, AUDIT_GUARD_NAME)):
        status = verify_audit(run_dir)
        if not status["ok"]:
            return {"granted": False, "labels_path": None, "reason": "audit_not_intact",
                    "exploratory": False, "audit": None, "audit_status": status}

        def deny(reason):
            rec = _append_audit(run_dir, dict(base, result="denied", reason=reason, exploratory=False))
            return {"granted": False, "labels_path": None, "reason": reason,
                    "exploratory": False, "audit": rec}

        manifest = statefile.read_json(os.path.join(run_dir, MANIFEST_NAME))
        if not manifest:
            return deny("no_manifest")
        base["manifest_hash"] = manifest.get("manifest_hash")
        checked = verify(run_dir)
        if not checked["ok"]:
            return deny(checked["reason"])
        plan_role, plan_err = plan_task_role(run_dir, task)
        if plan_err:
            return deny(plan_err)
        base["role"] = plan_role
        if role and role != plan_role:
            return deny("role_mismatch")
        if plan_role not in ALLOWED_ROLES:
            return deny("role_not_permitted")
        recipe = statefile.read_json(os.path.join(run_dir, RECIPE_LOCK_NAME))
        if not recipe:
            return deny("recipe_not_locked")
        base["recipe_version"] = recipe.get("model_version")
        threshold_path = resolve_ref(recipe.get("threshold_file", ""))
        if not os.path.isfile(threshold_path):
            return deny("threshold_missing")
        if sha256_file(threshold_path) != recipe.get("threshold_sha256"):
            return deny("recipe_changed")
        labels_path, labels_err = resolve_labels_path(manifest)
        if labels_err:
            return deny(labels_err)
        if not os.path.isfile(labels_path):
            return deny("labels_missing")
        exploratory = any(r.get("result") == "granted" for r in _read_audit_strict(audit_path)[0])
        rec = _append_audit(run_dir, dict(base, result="granted", reason="ok", exploratory=exploratory))
        return {"granted": True, "labels_path": labels_path, "reason": "ok",
                "exploratory": exploratory, "audit": rec}


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
    m.add_argument("--labels-dir", dest="labels_dir",
                   help="root chua nhan (ngoai checkout); mac dinh goc du an; cung nhan SEAL_LABELS_ROOT")
    m.add_argument("--out")
    m.add_argument("--actor")

    v = sub.add_parser("verify", help="kiem hash manifest/con nhan con khop")
    v.add_argument("run_dir")

    lk = sub.add_parser("lock", help="khoa recipe (model version + hash nguong)")
    lk.add_argument("run_dir")
    lk.add_argument("--model-version", required=True)
    lk.add_argument("--threshold-file", required=True)
    lk.add_argument("--actor")

    g = sub.add_parser("grant", help="xin duong dan nhan test theo role trong plan")
    g.add_argument("run_dir")
    g.add_argument("--task", required=True)
    g.add_argument("--purpose", required=True)
    g.add_argument("--role", help="chi de kiem cheo; phai khop role cua task trong plan")
    g.add_argument("--actor")
    g.add_argument("--dispatch")

    va = sub.add_parser("verify-audit", help="kiem chuoi hash + anchor cua seal_audit.jsonl")
    va.add_argument("run_dir")
    return ap


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "manifest":
            man = make_manifest(args.run_dir, args.dataset_version, args.split_id,
                                args.ids_file, args.labels_file, out=args.out,
                                actor=args.actor, labels_root=args.labels_dir)
            _print({k: man[k] for k in ("schema", "run_id", "dataset_version", "split_id", "n_samples",
                                        "ids_sha256", "labels_sha256", "labels_path", "labels_root",
                                        "manifest_hash", "manifest_path")})
            return 0
        if args.cmd == "verify":
            res = verify(args.run_dir)
            _print(res)
            return 0 if res["ok"] else 1
        if args.cmd == "lock":
            _print(lock_recipe(args.run_dir, args.model_version, args.threshold_file, actor=args.actor))
            return 0
        if args.cmd == "grant":
            res = grant(args.run_dir, args.role, args.task, args.purpose,
                        actor=args.actor, dispatch=args.dispatch)
            if not res["granted"]:
                print(f"TU CHOI: {res['reason']}", file=sys.stderr)
                return 1
            print(res["labels_path"])
            if res["exploratory"]:
                print("CANH BAO: mo lai seal -> ket qua la exploratory, KHONG phai blind final", file=sys.stderr)
            return 0
        if args.cmd == "verify-audit":
            res = verify_audit(args.run_dir)
            _print(res)
            return 0 if res["ok"] else 1
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
