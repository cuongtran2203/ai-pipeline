#!/usr/bin/env python3
"""Truy vet provenance cho dataset NGUON NGOAI (external data) cua AI pipeline.

Muc dich: khi chan doan ra DATA/STRUCTURE can dataset cong khai (vd chu so viet tay de train
ban phan loai cho field so), researcher tim + danh gia, nguoi duyet nguon & giay phep, roi dang
ky dataset ngoai mot cach TRUY VET duoc - khong phai "tai bua".

Script KHONG tai du lieu va KHONG dung mang. Viec tai do worker thuc hien trong container theo
luat sandbox SAU khi nguoi duyet; script chi ghi nhan va kiem (hash file/thu muc, duyet,
use-check). Nho vay provenance khong the bi "lam gia" bang mot URL khong kiem chung duoc.

State: runs/<id>/external_data/registry.json (read-modify-write qua statefile.update_json nen
nhieu tien trinh ghi dong thoi khong mat ban ghi).

Moi register/approve ghi Artifact + Decision (decided_by/approved_by Person) vao do thi tri thuc
qua kg.py API va mot dong so thi nghiem (notebook.py).

Commands:
  data_provenance.py register  <run_dir> [--card <file>] [cac co truong] [--id ID] [--requested-by NAME]
  data_provenance.py approve   <run_dir> <id> --approver person:<ten>
  data_provenance.py verify    <run_dir> <id> [--json]
  data_provenance.py list      <run_dir> [--json]
  data_provenance.py use-check <run_dir> <id>

Python standard library only.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statefile  # noqa: E402  (atomic + locked JSON writes shared by every writer)

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

REGISTRY_DIR = "external_data"
REGISTRY_NAME = "registry.json"
SCHEMA_VERSION = 1
_HEX = re.compile(r"^[0-9a-f]{64}$")
# Approver trong nhu agent (khong phai nguoi): bi tu choi du da co tien to person:.
_AGENT_NAME_RE = re.compile(
    r"^\s*(agent|bot|ai|system|codex|claude|gpt|pi|command[-_]?code|assistant|researcher|"
    r"module[-_]?dev|optimizer?|orchestrator)\b",
    re.IGNORECASE,
)

# Truong bat buoc trong dataset card (schema kiem trong script).
CARD_REQUIRED_STR = (
    "name", "version", "source_url", "license", "domain", "intended_use",
    "domain_shift_assessment", "location",
)


class ProvenanceError(ValueError):
    """Dataset card / thao tac provenance vi pham hop dong."""


# --------------------------------------------------------------------------- helpers

def registry_path(run_dir):
    return os.path.join(os.path.abspath(run_dir), REGISTRY_DIR, REGISTRY_NAME)


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kg_ts():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _slug(text, n=64):
    s = re.sub(r"[^\w\- ]+", "", str(text), flags=re.U).strip().replace(" ", "-")
    return (s[:n] or "entry").strip("-").lower()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_location(location):
    """Tinh lai hash cua 1 file hoac thu muc. KHONG mang, KHONG tai."""
    if os.path.isfile(location):
        return {"kind": "file", "sha256": _sha256_file(location),
                "size_bytes": os.path.getsize(location), "manifest": None}
    if os.path.isdir(location):
        manifest = {}
        total = 0
        for root, dirs, files in os.walk(location):
            dirs.sort()
            for fn in sorted(files):
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, location).replace("\\", "/")
                manifest[rel] = _sha256_file(p)
                try:
                    total += os.path.getsize(p)
                except OSError:
                    pass
        agg = _sha256_text("\n".join(f"{k}:{manifest[k]}" for k in sorted(manifest)))
        return {"kind": "dir", "sha256": agg, "size_bytes": total, "manifest": manifest}
    raise ProvenanceError(f"location khong ton tai hoac khong phai file/thu muc: {location}")


# --------------------------------------------------------------------------- card schema

def validate_card(card):
    """Kiem schema dataset card; tra ban chuan hoa (khong ghi gi). Raise ProvenanceError."""
    if not isinstance(card, dict):
        raise ProvenanceError("dataset card phai la mot JSON object")

    out = {}
    for key in CARD_REQUIRED_STR:
        val = card.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ProvenanceError(
                f"dataset card thieu truong '{key}' hoac gia tri khong phai chuoi khong rong")
        out[key] = val.strip()

    license_ok = card.get("license_ok")
    if not isinstance(license_ok, bool):
        raise ProvenanceError("truong 'license_ok' phai la true/false (da xac nhan giay phep hay chua)")
    out["license_ok"] = license_ok

    size_bytes = card.get("size_bytes")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        raise ProvenanceError("truong 'size_bytes' phai la so nguyen >= 0")
    out["size_bytes"] = size_bytes

    retrieved_at = card.get("retrieved_at")
    if retrieved_at is None:
        retrieved_at = _now_iso()
    if not isinstance(retrieved_at, str) or not retrieved_at.strip():
        raise ProvenanceError("truong 'retrieved_at' phai la chuoi thoi diem")
    out["retrieved_at"] = retrieved_at.strip()

    archive = card.get("archive_sha256")
    if archive is not None:
        if not isinstance(archive, str) or not _HEX.match(archive.lower()):
            raise ProvenanceError("'archive_sha256' phai la chuoi hex sha256 (64 ky tu)")
        archive = archive.lower()
    manifest = card.get("manifest")
    if manifest is not None:
        if not isinstance(manifest, dict) or not manifest:
            raise ProvenanceError("'manifest' phai la object {duong_dan: sha256} khong rong")
        clean = {}
        for k, v in manifest.items():
            if not isinstance(k, str) or not k.strip():
                raise ProvenanceError("'manifest' co key khong hop le")
            if not isinstance(v, str) or not _HEX.match(v.lower()):
                raise ProvenanceError(f"'manifest' muc '{k}' phai la sha256 hex 64 ky tu")
            clean[k.strip().replace("\\", "/")] = v.lower()
        manifest = clean
    if not archive and not manifest:
        raise ProvenanceError("can co 'archive_sha256' hoac 'manifest' (hash tung file) de kiem sau nay")
    out["archive_sha256"] = archive
    out["manifest"] = manifest

    card_id = card.get("id")
    if card_id is not None:
        if not isinstance(card_id, str) or not card_id.strip():
            raise ProvenanceError("'id' neu co phai la chuoi khong rong")
        out["id"] = card_id.strip()
    for opt in ("pilot_result", "notes"):
        if card.get(opt) is not None:
            out[opt] = card[opt]
    return out


def build_entry(card, actor="researcher", now=None):
    """Tu dataset card tao entry registry (chua ghi). id suy ra tu name+version neu thieu."""
    c = validate_card(card)
    entry_id = c.get("id") or f"ext-{_slug(c['name'])}-{_slug(c['version'])}"
    now = now or _now_iso()
    return {
        "id": entry_id,
        "name": c["name"],
        "version": c["version"],
        "source_url": c["source_url"],
        "license": c["license"],
        "license_ok": c["license_ok"],
        "retrieved_at": c["retrieved_at"],
        "archive_sha256": c["archive_sha256"],
        "manifest": c["manifest"],
        "size_bytes": c["size_bytes"],
        "domain": c["domain"],
        "intended_use": c["intended_use"],
        "domain_shift_assessment": c["domain_shift_assessment"],
        "location": c["location"],
        "approved_by": None,
        "approved_at": None,
        "pilot_result": c.get("pilot_result"),
        "notes": c.get("notes"),
        "registered_at": now,
        "registered_by": actor,
    }


# --------------------------------------------------------------------------- registry IO

def load_registry(run_dir):
    data = statefile.read_json(registry_path(run_dir))
    if not isinstance(data, dict):
        return {"schema_version": SCHEMA_VERSION, "entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def get_entry(run_dir, entry_id):
    return load_registry(run_dir)["entries"].get(entry_id)


def _comparable(entry):
    """Phan entry dung de so sanh idempotency (bo moc thoi gian/tham chieu)."""
    return {k: v for k, v in entry.items() if k not in ("registered_at", "registered_by",
                                                        "approved_by", "approved_at")}


def register(run_dir, card, actor="researcher"):
    """Ghi 1 dataset ngoai vao registry. Idempotent theo id. Tra (entry, created_bool)."""
    entry = build_entry(card, actor=actor)
    holder = {}

    def fn(data):
        if not isinstance(data, dict):
            data = {"schema_version": SCHEMA_VERSION, "entries": {}}
        entries = data.setdefault("entries", {})
        existing = entries.get(entry["id"])
        if existing is not None:
            if _comparable(existing) != _comparable(entry):
                raise ProvenanceError(
                    f"dataset id '{entry['id']}' da ton tai voi noi dung khac; "
                    "dung id moi hoac --id khac (khong ghi de ngam)")
            holder["entry"] = existing
            holder["created"] = False
            return data
        entries[entry["id"]] = entry
        holder["entry"] = entry
        holder["created"] = True
        return data

    statefile.update_json(registry_path(run_dir), fn,
                          default={"schema_version": SCHEMA_VERSION, "entries": {}})
    if holder["created"]:
        _after_change(run_dir, "register", holder["entry"])
    return holder["entry"], holder["created"]


def parse_approver(approver):
    """Tra (approved_by, ten). Chi chap nhan 'person:<ten>', tu choi agent/rong."""
    if not isinstance(approver, str) or not approver.strip():
        raise ProvenanceError(
            "approve can --approver 'person:<ten nguoi>' (nguoi that); gia tri rong khong hop le")
    a = approver.strip()
    if not a.lower().startswith("person:"):
        raise ProvenanceError(
            "approve chi do NGUOI: --approver phai bat dau bang 'person:' "
            "(vd person:Nguyen Van A), khong nhan agent/ten may")
    name = a.split(":", 1)[1].strip()
    if not name:
        raise ProvenanceError("approve: thieu ten nguoi sau 'person:'")
    if _AGENT_NAME_RE.match(name):
        raise ProvenanceError(f"approve bi tu choi: '{name}' trong nhu agent, khong phai nguoi")
    return a, name


def approve(run_dir, entry_id, approver):
    """Nguoi duyet nguon du lieu. Idempotent khi cung nguoi duyet. Tra (entry, changed_bool)."""
    approved_by, person_name = parse_approver(approver)
    holder = {}

    def fn(data):
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            raise ProvenanceError("registry chua co entry nao de duyet")
        entry = data["entries"].get(entry_id)
        if entry is None:
            raise ProvenanceError(f"khong tim thay dataset id '{entry_id}' trong registry")
        if entry.get("approved_by") == approved_by:
            holder["entry"] = entry
            holder["changed"] = False
            return data
        entry["approved_by"] = approved_by
        entry["approved_at"] = _now_iso()
        holder["entry"] = entry
        holder["changed"] = True
        return data

    statefile.update_json(registry_path(run_dir), fn,
                          default={"schema_version": SCHEMA_VERSION, "entries": {}})
    if holder["changed"]:
        _after_change(run_dir, "approve", holder["entry"], approver_name=person_name,
                      approver_raw=approved_by)
    return holder["entry"], holder["changed"]


# --------------------------------------------------------------------------- checks

def check_hash(entry, location=None):
    """So hash hien tai voi hash ghi trong entry. Tra dict {ok, reason, ...}."""
    loc = location or entry.get("location")
    if not loc:
        return {"ok": False, "reason": "entry thieu 'location'"}
    if not os.path.exists(loc):
        return {"ok": False, "reason": f"location khong ton tai: {loc} (chua tai trong container?)"}
    got = hash_location(loc)
    if got["kind"] == "file":
        exp = (entry.get("archive_sha256") or "").lower()
        if not exp:
            return {"ok": False, "reason": "ban ghi khong co archive_sha256 cho file", "actual": got}
        ok = exp == got["sha256"]
        return {"ok": ok, "reason": "khop" if ok else "hash file khong khop",
                "expected": exp, "actual": got}
    if entry.get("manifest"):
        exp = {k: v.lower() for k, v in entry["manifest"].items()}
        gotm = got["manifest"]
        missing = sorted(set(exp) - set(gotm))
        extra = sorted(set(gotm) - set(exp))
        changed = sorted(k for k in exp if k in gotm and exp[k] != gotm[k])
        ok = not (missing or extra or changed)
        return {"ok": ok, "reason": "khop" if ok else "manifest thu muc khong khop",
                "missing": missing, "extra": extra, "changed": changed, "actual": got}
    exp = (entry.get("archive_sha256") or "").lower()
    if not exp:
        return {"ok": False, "reason": "ban ghi khong co hash (archive/manifest)", "actual": got}
    ok = exp == got["sha256"]
    return {"ok": ok, "reason": "khop" if ok else "hash thu muc khong khop",
            "expected": exp, "actual": got}


def use_check(run_dir, entry_id, location=None):
    """Cong bat buoc module-dev/optimize goi TRUOC khi train. Tra dict {ok, reason}."""
    entry = get_entry(run_dir, entry_id)
    if entry is None:
        return {"ok": False, "reason": f"khong tim thay dataset id '{entry_id}' trong registry"}
    if not entry.get("approved_by"):
        return {"ok": False, "reason": "dataset CHUA duoc nguoi duyet (thieu approved_by)", "entry": entry}
    if entry.get("license_ok") is not True:
        return {"ok": False, "reason": "giay phep CHUA duoc xac nhan (license_ok=false)", "entry": entry}
    if not str(entry.get("domain_shift_assessment") or "").strip():
        return {"ok": False, "reason": "thieu danh gia lech phan bo (domain_shift_assessment)", "entry": entry}
    got = check_hash(entry, location=location)
    if not got["ok"]:
        return {"ok": False, "reason": "hash khong khop: " + got["reason"], "hash": got, "entry": entry}
    return {"ok": True, "reason": "ok", "entry": entry, "hash": got}


def verify(run_dir, entry_id, location=None):
    """Tinh lai hash file/thu muc (khong tai, khong mang) va doi chieu entry. Tra dict."""
    entry = get_entry(run_dir, entry_id)
    if entry is None:
        return {"ok": False, "reason": f"khong tim thay dataset id '{entry_id}' trong registry"}
    got = check_hash(entry, location=location)
    return {"id": entry_id, "location": entry.get("location"), **got}


# --------------------------------------------------------------------------- KG + notebook

def _rel(run_dir, path):
    try:
        import kg  # noqa: E402
        rel = kg.normalize_ref_path(path, root=os.path.abspath(run_dir))
        if rel:
            return rel
    except Exception:  # noqa: BLE001
        pass
    return os.path.abspath(path).replace("\\", "/")


def _log_notebook(run_dir, etype, title, body, author, refs):
    try:
        import argparse as _ap
        import notebook  # noqa: E402
        ns = _ap.Namespace(run_dir=run_dir, type=etype, title=title, body=body,
                           tags="external_data,provenance", metrics="{}",
                           refs=",".join(refs), author=author, no_kg=True, kg_edges=None)
        notebook.cmd_log(ns)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [CANH BAO] khong ghi duoc so thi nghiem ({e})", file=sys.stderr)
        return False


def _write_kg(run_dir, entry, action, approver_name=None, approver_raw=None):
    """Ghi Artifact + Decision + Person vao KG qua kg.py API. Tra True/False."""
    try:
        import kg  # noqa: E402
    except Exception as e:  # noqa: BLE001
        print(f"  [CANH BAO] khong import duoc kg.py ({e}); bo qua ghi KG", file=sys.stderr)
        return False
    ts = _kg_ts()
    reg_rel = _rel(run_dir, registry_path(run_dir))
    art_id = "artifact:extdata-" + kg.slug(entry["id"])
    try:
        kg.upsert_entity(
            run_dir, art_id, "Artifact", f"Dataset ngoai: {entry['name']}",
            body=entry.get("domain_shift_assessment") or "",
            properties={"kind": "external_dataset", "dataset_id": entry["id"],
                        "version": entry.get("version"), "registry": reg_rel,
                        "source_url": entry.get("source_url"), "license": entry.get("license"),
                        "location": entry.get("location"), "intended_use": entry.get("intended_use")},
            created_at=ts)
        if action == "register":
            actor = entry.get("registered_by") or "researcher"
            person_id = "person:" + kg.slug(actor)
            dec_id = "decision:extdata-register-" + kg.slug(entry["id"])
            kg.upsert_entity(run_dir, person_id, "Person", actor,
                             properties={"alias": actor}, created_at=ts)
            kg.upsert_entity(
                run_dir, dec_id, "Decision", f"Dang ky dataset ngoai: {entry['name']}",
                body=entry.get("domain_shift_assessment") or "",
                properties={"dataset_id": entry["id"], "intended_use": entry.get("intended_use")},
                created_at=ts)
            kg.add_edge_checked(run_dir, dec_id, person_id, "decided_by", valid_from=ts,
                                recorded_at=ts, source_ref=reg_rel)
            kg.add_edge_checked(run_dir, dec_id, art_id, "uses", valid_from=ts,
                                recorded_at=ts, source_ref=reg_rel)
        else:  # approve
            person_id = "person:" + kg.slug(approver_name or "unknown")
            dec_id = "decision:extdata-approve-" + kg.slug(entry["id"])
            kg.upsert_entity(run_dir, person_id, "Person", approver_raw or approver_name,
                             properties={"alias": approver_name}, created_at=ts)
            kg.upsert_entity(
                run_dir, dec_id, "Decision", f"Duyet dataset ngoai: {entry['name']}",
                body=f"license={entry.get('license')}; approved_at={entry.get('approved_at')}",
                properties={"dataset_id": entry["id"], "approved_by": entry.get("approved_by")},
                created_at=ts)
            kg.add_edge_checked(run_dir, dec_id, person_id, "approved_by", valid_from=ts,
                                recorded_at=ts, source_ref=reg_rel)
            kg.add_edge_checked(run_dir, dec_id, art_id, "uses", valid_from=ts,
                                recorded_at=ts, source_ref=reg_rel)
            reg_dec = "decision:extdata-register-" + kg.slug(entry["id"])
            try:
                kg.add_edge_checked(run_dir, dec_id, reg_dec, "supersedes", valid_from=ts,
                                    recorded_at=ts, source_ref=reg_rel)
            except Exception:  # noqa: BLE001 - register decision co the thieu
                pass
        return True
    except Exception as e:  # noqa: BLE001 - KG loi khong lam hong provenance
        print(f"  [CANH BAO] khong ghi duoc KG ({e})", file=sys.stderr)
        return False


def _after_change(run_dir, action, entry, approver_name=None, approver_raw=None):
    """Side effects sau khi registry da doi: KG + 1 dong so thi nghiem."""
    kg_ok = _write_kg(run_dir, entry, action, approver_name=approver_name, approver_raw=approver_raw)
    reg_rel = _rel(run_dir, registry_path(run_dir))
    if action == "register":
        _log_notebook(
            run_dir, "research", f"Dang ky dataset ngoai: {entry['name']}",
            (f"Nguon: {entry['source_url']} | license={entry['license']} (license_ok={entry['license_ok']}) "
             f"| domain={entry['domain']} | intended_use={entry['intended_use']}\n"
             f"Danh gia lech phan bo: {entry['domain_shift_assessment']}\n"
             f"location={entry['location']} | version={entry['version']} | KG={'ok' if kg_ok else 'pending'}"),
            entry.get("registered_by") or "researcher", [reg_rel])
    else:
        _log_notebook(
            run_dir, "decision", f"Nguoi duyet dataset ngoai: {entry['name']}",
            (f"Nguoi duyet: {entry.get('approved_by')} luc {entry.get('approved_at')}. "
             f"license={entry.get('license')}, license_ok={entry.get('license_ok')}. "
             f"Chi dung de train/pretrain, KHONG vao val/test. KG={'ok' if kg_ok else 'pending'}."),
            approver_name or "person", [reg_rel])
    return kg_ok


# --------------------------------------------------------------------------- CLI

def _print_list(run_dir, as_json):
    reg = load_registry(run_dir)
    entries = [reg["entries"][k] for k in sorted(reg["entries"])]
    if as_json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    if not entries:
        print("(registry rong)")
        return 0
    print(f"{'ID':<28} {'VERSION':<14} {'LIC':<4} {'APPROVED_BY':<22} DOMAIN")
    for e in entries:
        print(f"{e['id']:<28} {str(e.get('version') or '-'):<14} "
              f"{'yes' if e.get('license_ok') else 'no':<4} "
              f"{str(e.get('approved_by') or '-'):<22} {e.get('domain') or '-'}")
    return 0


def cmd_register(a):
    card = {}
    if a.card:
        with open(a.card, encoding="utf-8-sig") as f:
            card = json.load(f)
        if not isinstance(card, dict):
            raise ProvenanceError("file --card phai la JSON object")
    for key in ("id", "name", "version", "source_url", "license", "domain", "intended_use",
                "domain_shift_assessment", "location", "retrieved_at", "pilot_result"):
        val = getattr(a, key, None)
        if val is not None:
            card[key] = val
    if a.license_ok is not None:
        card["license_ok"] = a.license_ok
    if a.size_bytes is not None:
        card["size_bytes"] = a.size_bytes
    if a.archive_sha256 is not None:
        card["archive_sha256"] = a.archive_sha256
    if a.manifest is not None:
        card["manifest"] = json.loads(a.manifest)
    entry, created = register(a.run_dir, card, actor=a.requested_by)
    print(json.dumps({"id": entry["id"], "created": created, "approved_by": entry["approved_by"]},
                     ensure_ascii=False))
    if not created:
        print(f"  (da ton tai, khong doi - idempotent)", file=sys.stderr)
    return 0


def cmd_approve(a):
    entry, changed = approve(a.run_dir, a.id, a.approver)
    print(json.dumps({"id": entry["id"], "approved_by": entry["approved_by"],
                      "approved_at": entry["approved_at"], "changed": changed}, ensure_ascii=False))
    if not changed:
        print("  (da duyet boi cung nguoi nay - idempotent)", file=sys.stderr)
    return 0


def cmd_verify(a):
    res = verify(a.run_dir, a.id)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"[{'OK' if res['ok'] else 'FAIL'}] {a.id}: {res['reason']}")
    return 0 if res["ok"] else 1


def cmd_list(a):
    return _print_list(a.run_dir, a.json)


def cmd_use_check(a):
    res = use_check(a.run_dir, a.id)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"[{'OK' if res['ok'] else 'CHAN'}] {a.id}: {res['reason']}")
    return 0 if res["ok"] else 1


def _add_card_args(p):
    p.add_argument("--card", help="file JSON dataset card (templates/dataset_card.template.json)")
    p.add_argument("--id")
    p.add_argument("--name")
    p.add_argument("--version")
    p.add_argument("--source-url")
    p.add_argument("--license")
    p.add_argument("--license-ok", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--domain")
    p.add_argument("--intended-use")
    p.add_argument("--domain-shift-assessment")
    p.add_argument("--location")
    p.add_argument("--size-bytes", type=int, default=None)
    p.add_argument("--retrieved-at")
    p.add_argument("--archive-sha256")
    p.add_argument("--manifest", help="JSON object {duong_dan: sha256}")
    p.add_argument("--pilot-result")
    p.add_argument("--requested-by", default="researcher",
                   help="nguon yeu cau dang ky (mac dinh researcher)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register", help="dang ky 1 dataset ngoai vao registry (truy vet)")
    p.add_argument("run_dir")
    _add_card_args(p)
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("approve", help="NGUOI duyet nguon + giay phep (approved_by=person:...)")
    p.add_argument("run_dir")
    p.add_argument("id")
    p.add_argument("--approver", required=True, help="person:<ten nguoi>")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("verify", help="tinh lai hash file/thu muc (khong tai, khong mang)")
    p.add_argument("run_dir")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("list", help="liet ke dataset ngoai trong registry")
    p.add_argument("run_dir")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("use-check", help="cong bat buoc truoc khi train (module-dev/optimize goi)")
    p.add_argument("run_dir")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_use_check)

    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except ProvenanceError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"LỖI: khong doc duoc file ({e})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
