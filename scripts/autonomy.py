#!/usr/bin/env python3
"""Autonomy policy + audit log cho human-on-the-loop (ai-pipeline).

Policy theo run (runs/<id>/autonomy_policy.json, xem templates/autonomy_policy.template.json):
mode theo phase (manual|bounded_auto|approval_required), loai thao tac duoc phep,
cap task/tien API/thoi gian/GPU-gio/token, nguong canh bao, hanh vi khi cham tran,
version policy.

Audit log append-only: runs/<id>/audit.jsonl (actor, UTC timestamp, scope,
policy version, quyet dinh, ly do, refs). Event approve/gate duoc noi thanh
canh approved_by/decided_by vao lop KG cua T2 (scripts/kg.py).

CLI:
  autonomy.py validate <policy.json>
  autonomy.py check <run_dir> --action start --phase build [--task T3] [--approved]
  autonomy.py approve <run_dir> --scope T3 --decision approve --reason "..." [--actor person:user]
  autonomy.py log <run_dir> --event start --scope T3 --decision started --reason "..." [--actor X]

API cho plan_to_herdr / coordinator:
  from autonomy import policy_status, read_usage_strict, check_action, append_audit,
      derive_tasks_started, read_admission, read_started_map

Stdlib only.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import uuid

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import statefile as statefile_mod
except ImportError:  # pragma: no cover - scripts/ luon di kem
    statefile_mod = None

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_FILE = "autonomy_policy.json"
AUDIT_FILE = "audit.jsonl"
USAGE_FILE = "usage.json"

MODES = ("manual", "bounded_auto", "approval_required")
PHASES = ("intake", "analysis", "planning", "build", "integration", "optimize", "release")
ACTIONS = ("ask", "start", "stop", "release", "roster_change", "policy_change", "override", "gate")
APPROVAL_EVENTS = {"gate", "approve", "release", "policy_change", "override"}
CAP_KEYS = ("max_tasks", "max_api_cost_usd", "max_elapsed_hours", "max_gpu_hours", "max_tokens")
USAGE_OF = {"max_tasks": "tasks_started", "max_api_cost_usd": "api_cost_usd",
            "max_elapsed_hours": "elapsed_hours", "max_gpu_hours": "gpu_hours",
            "max_tokens": "tokens"}


def utcnow():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"


def policy_path(run_dir, override=None):
    if override:
        return override
    return os.path.join(run_dir, POLICY_FILE)


def load_policy(run_dir, override=None):
    """Doc policy; None neu run chua co policy (tuong thich nguoc: run cu van chay).

    Giu nguyen chu ky vi project_status/supervisor dung de hien thi (read-only).
    Duong start (plan_to_herdr --start-ready) KHONG dung ham nay ma dung
    policy_status() de phan biet missing (cho phep + audit migration) voi
    corrupt/invalid (fail-closed: dung start).
    """
    p = policy_path(run_dir, override)
    try:
        with statefile_mod.open_read(p, encoding="utf-8-sig") as f:  # -sig: chiu duoc BOM do PowerShell ghi
            return json.load(f)
    except (OSError, ValueError):
        return None


def policy_status(run_dir, override=None):
    """Phan biet trang thai policy cho duong start (fail-closed).

    Tra ve (status, policy, errors) voi status thuoc:
      missing - KHONG CO file (run cu) -> cho phep + canh bao + audit migration
      ok      - hop le -> check cap binh thuong
      corrupt - file TON TAI nhung rong / doc-parse loi -> DUNG start, exit != 0
      invalid - parse duoc nhung validate loi -> DUNG start, exit != 0
    Chi file KHONG TON TAI moi la legacy-missing; file da ton tai ma rong
    hoac sai kieu/schema la corrupt (fail-closed, KHONG ghi de, RV4 P2).
    """
    p = policy_path(run_dir, override)
    try:
        with statefile_mod.open_read(p, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return "missing", None, []
    except OSError as e:
        return "corrupt", None, [f"khong doc duoc policy {p}: {e}"]
    if not text.strip():
        return "corrupt", None, [f"autonomy_policy.json ton tai nhung rong ({p}); "
                                 "sua/xoa tay hoac khoi phuc ban sao, KHONG tu ghi de"]
    try:
        pol = json.loads(text)
    except ValueError as e:
        return "corrupt", None, [f"autonomy_policy.json khong phai JSON hop le ({e}); "
                                 "sua/xoa tay hoac khoi phuc ban sao, KHONG tu ghi de"]
    errs = validate_policy(pol)
    if errs:
        return "invalid", pol, errs
    return "ok", pol, []


def validate_policy(p):
    """Tra ve list loi (rong = hop le). Khong exit: CLI tu exit."""
    errs = []
    if not isinstance(p, dict):
        return ["policy phai la object JSON"]
    for k in ("run_id", "policy_version", "default_mode", "modes", "allowed_actions", "caps", "warn_at", "on_cap"):
        if k not in p:
            errs.append(f"thieu truong bat buoc '{k}'")
    if p.get("default_mode") not in MODES:
        errs.append(f"default_mode phai thuoc {MODES}")
    modes = p.get("modes") or {}
    for ph, m in modes.items():
        if ph not in PHASES:
            errs.append(f"modes: phase la '{ph}' (mot trong: {', '.join(PHASES)})")
        if m not in MODES:
            errs.append(f"modes.{ph}: mode '{m}' khong hop le {MODES}")
    for a in p.get("allowed_actions") or []:
        if a not in ACTIONS:
            errs.append(f"allowed_actions: '{a}' khong hop le (mot trong: {', '.join(ACTIONS)})")
    caps = p.get("caps") or {}
    for k in caps:
        if k not in CAP_KEYS:
            errs.append(f"caps: '{k}' khong hop le (mot trong: {', '.join(CAP_KEYS)})")
    w = p.get("warn_at")
    if not isinstance(w, (int, float)) or not (0 < w < 1):
        errs.append("warn_at phai la so trong (0, 1), vd. 0.8")
    if p.get("on_cap") not in ("require_approval", "pause", "stop"):
        errs.append("on_cap phai la require_approval|pause|stop")
    return errs


def read_usage(run_dir, override=None):
    """Doc bo dem tich luy runs/<id>/usage.json; thieu file thi coi nhu {} (chua biet).

    Giu nguyen hanh vi hien thi (lenient) cho project_status/supervisor (read-only).
    Duong start dung read_usage_strict(): file hong -> StateCorrupt (dung, khong ghi de).

    Phan biet 'chua biet' voi 0: file/khoa vang khong co nghia la da do duoc 0.
    check_action() bao 'usage unknown' cho cap tien/token/GPU khong co nguon do
    thay vi lang le cho qua.
    """
    p = override or os.path.join(run_dir, USAGE_FILE)
    try:
        with statefile_mod.open_read(p, encoding="utf-8-sig") as f:  # -sig: chiu duoc BOM do PowerShell ghi
            u = json.load(f)
        return u if isinstance(u, dict) else {}
    except (OSError, ValueError):
        return {}


def _validate_usage_records(u, path):
    """Validate kieu ban ghi usage TRUOC khi tinh cap (RV4 P2, fail-closed).

    usage phai la dict; moi khoa usage biet (tasks_started, api_cost_usd,
    elapsed_hours, gpu_hours, tokens) neu co phai la int/float (bool bi loai).
    Sai -> StateCorrupt (KHONG coi nhu {}, KHONG ghi de).
    """
    if not isinstance(u, dict):
        raise statefile_mod.StateCorrupt(
            f"{path} phai la object JSON, got {type(u).__name__}; khong ghi de. "
            "Sua/xoa tay hoac khoi phuc ban sao.")
    for k in USAGE_OF.values():
        if k in u and u[k] is not None and not isinstance(u[k], (int, float)):
            raise statefile_mod.StateCorrupt(
                f"{path}['{k}'] phai la so, got {type(u[k]).__name__}; khong ghi de. "
                "Sua/xoa tay hoac khoi phuc ban sao.")
        if isinstance(u.get(k), bool):
            raise statefile_mod.StateCorrupt(
                f"{path}['{k}'] phai la so, got bool; khong ghi de. "
                "Sua/xoa tay hoac khoi phuc ban sao.")
    return u


def read_usage_strict(run_dir, override=None):
    """Doc usage.json cho duong start: hong -> StateCorrupt (KHONG ghi de, dung start).

    Chi file KHONG TON TAI moi la legacy-missing (-> {} = chua start gi).
    File TON TAI ma rong hoac khong phai object (vd. list) la corrupt
    (fail-closed, RV4 P2). Kieu ban ghi duoc validate truoc khi tinh cap.
    """
    p = override or os.path.join(run_dir, USAGE_FILE)
    try:
        with statefile_mod.open_read(p, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return {}
    if not text.strip():
        raise statefile_mod.StateCorrupt(
            f"{p} ton tai nhung rong; khong ghi de. "
            "Sua/xoa tay hoac khoi phuc ban sao.")
    try:
        u = json.loads(text)
    except ValueError as e:
        raise statefile_mod.StateCorrupt(
            f"{p} khong phai JSON hop le ({e}); khong ghi de. "
            "Sua/xoa tay hoac khoi phuc ban sao.") from e
    return _validate_usage_records(u, p)


def write_usage(run_dir, usage, override=None):
    """Ghi usage.json qua statefile (khoa + atomic temp/replace).

    File hong -> StateCorrupt, KHONG bao gio ghi de im lang (fail-closed).
    """
    p = override or os.path.join(run_dir, USAGE_FILE)
    return statefile_mod.update_json(p, lambda _old: usage, default={})


def reserve_task_quota(run_dir, n=1, usage=None, override=None):
    """Cong don tasks_started ngay vao usage.json (atomic, qua statefile).

    Giu lai de tuong thich (test/unit cu); duong start moi (plan_to_herdr
    --start-ready) KHONG dung ham nay nua ma suy tasks_started tu
    admission/started (derive_tasks_started) theo tung task ID.
    File hong -> StateCorrupt (khong ghi de).
    """
    u = dict(usage) if usage is not None else read_usage(run_dir, override)
    u["tasks_started"] = (u.get("tasks_started") or 0) + n
    return write_usage(run_dir, u, override)


# --- Admission + started (nguon su that cho quota, doc ca dang cu) ---

ADMISSION_FILE = "admission.json"
# Trang thai giu quota (duoc dem vao tasks_started); "failed" la da giai phong.
ADMISSION_ACTIVE = ("reserved", "starting", "started")


def admission_path(run_dir):
    return os.path.join(run_dir, ADMISSION_FILE)


def read_started_map(run_dir):
    """started.json dang dict (moi) hoac list (cu) -> dict {task_id: receipt}.

    Chi file KHONG TON TAI moi la legacy-missing (-> {}). File TON TAI ma
    rong la corrupt (fail-closed, RV4 P2). Hong hoac khong phai dict/list ->
    StateCorrupt (dung, KHONG ghi de).
    """
    p = os.path.join(run_dir, "started.json")
    try:
        with statefile_mod.open_read(p, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return {}
    if not text.strip():
        raise statefile_mod.StateCorrupt(
            f"{p} ton tai nhung rong; khong ghi de. "
            "Sua/xoa tay hoac khoi phuc ban sao.")
    try:
        raw = json.loads(text)
    except ValueError as e:
        raise statefile_mod.StateCorrupt(
            f"{p} khong phai JSON hop le ({e}); khong ghi de. "
            "Sua/xoa tay hoac khoi phuc ban sao.") from e
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, list):
        return {tid: True for tid in raw}
    raise statefile_mod.StateCorrupt(
        f"{p} phai la object (moi) hoac list (cu), got {type(raw).__name__}; khong ghi de.")


ADMISSION_STATES = ("reserved", "starting", "started", "failed")


def validate_admission(adm, path="admission.json"):
    """Validate kieu ban ghi admission TRUOC khi tinh cap (RV4 P2, fail-closed).

    adm phai la dict; moi ban ghi phai la dict co state thuoc
    reserved|starting|started|failed; owner/generation/dispatch neu co phai
    la str (hoac None). Sai -> StateCorrupt (KHONG bo qua im lang).
    """
    if not isinstance(adm, dict):
        raise statefile_mod.StateCorrupt(
            f"{path} phai la object, got {type(adm).__name__}; khong ghi de.")
    for tid, rec in adm.items():
        if not isinstance(rec, dict):
            raise statefile_mod.StateCorrupt(
                f"{path}['{tid}'] phai la object, got {type(rec).__name__}; khong ghi de.")
        if rec.get("state") not in ADMISSION_STATES:
            raise statefile_mod.StateCorrupt(
                f"{path}['{tid}'].state phai thuoc {ADMISSION_STATES}, "
                f"got {rec.get('state')!r}; khong ghi de.")
        for fk in ("owner", "generation", "dispatch", "ts"):
            if fk in rec and rec[fk] is not None and not isinstance(rec[fk], str):
                raise statefile_mod.StateCorrupt(
                    f"{path}['{tid}'].{fk} phai la str, got {type(rec[fk]).__name__}; khong ghi de.")
    return adm


def read_admission(run_dir):
    """Doc admission.json: thieu -> {}; TON TAI ma rong/sai kieu -> StateCorrupt (RV4 P2)."""
    p = admission_path(run_dir)
    try:
        with statefile_mod.open_read(p, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return {}
    if not text.strip():
        raise statefile_mod.StateCorrupt(
            f"{p} ton tai nhung rong; khong ghi de. "
            "Sua/xoa tay hoac khoi phuc ban sao.")
    try:
        adm = json.loads(text)
    except ValueError as e:
        raise statefile_mod.StateCorrupt(
            f"{p} khong phai JSON hop le ({e}); khong ghi de. "
            "Sua/xoa tay hoac khoi phuc ban sao.") from e
    return validate_admission(adm, p)


def derive_tasks_started(run_dir):
    """Suy tasks_started tu admission (reserved/starting/started).

    Thay cho cong thu cong: quota duoc giai phong khi task -> failed,
    nen so lieu khong bao gio treo sau start loi. Gate (G1/G2/G3...) khong
    bao gio vao admission nen khong bi dem. Key started.json chi duoc dem
    khi co ban ghi admission mirror (worker start qua admission luon co);
    key started mo coi (plan doi, task mat) khong dem - huong fail-closed
    that (thieu hon thua) thi chay --reconcile de nhap lai.
    """
    adm = read_admission(run_dir)
    active = {tid for tid, rec in adm.items()
              if isinstance(rec, dict) and rec.get("state") in ADMISSION_ACTIVE}
    started = read_started_map(run_dir)  # hong -> StateCorrupt (fail-closed, khong dem bay)
    mirrored = {tid for tid in started if tid in adm}
    return len(active | mirrored)


def mode_of(policy, phase):
    if not phase:
        return policy.get("default_mode", "bounded_auto")
    return (policy.get("modes") or {}).get(phase, policy.get("default_mode", "bounded_auto"))


def check_action(policy, action, phase=None, usage=None, approved=False):
    """Kiem tra 1 hanh dong theo policy.

    Tra ve (allowed: bool, reason: str, warnings: [str]).
    """
    usage = usage or {}
    warnings = []
    if action not in (policy.get("allowed_actions") or []):
        return False, f"policy {policy.get('policy_version')}: action '{action}' khong thuoc allowed_actions", warnings
    mode = mode_of(policy, phase)
    if mode == "manual":
        return False, f"phase '{phase or '-'}' che do manual: can nguoi thuc hien, khong tu dong", warnings
    if mode == "approval_required" and not approved:
        return False, f"phase '{phase or '-'}' che do approval_required: can approve truoc (autonomy.py approve)", warnings
    caps = policy.get("caps") or {}
    warn_at = policy.get("warn_at", 0.8)
    for cap_key in CAP_KEYS:
        cap = caps.get(cap_key)
        if cap is None:
            continue
        used_key = USAGE_OF[cap_key]
        if used_key not in usage and cap_key != "max_tasks":
            # So do chua biet (khong co nguon do/telemetry), khac voi da do duoc 0:
            # van cho phep (tuong thich) nhung canh bao ro thay vi lang le cho qua.
            # Rieng max_tasks: file usage vang dong nghia chua start task nao (= 0).
            warnings.append(f"usage unknown: '{used_key}' chua co so do (coi nhu 0) "
                            f"-- can nguon do/telemetry cho cap {cap_key}")
        used = usage.get(used_key, 0) or 0
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            # Ban ghi sai kieu lot qua doc lenient: fail-closed, khong crash TypeError (RV4 P2).
            return False, (f"usage '{used_key}' sai kieu (got {type(used).__name__}, "
                           f"can so); fail-closed. Sua usage.json roi chay lai"), warnings
        if used >= cap:
            return False, (f"cham tran {cap_key} ({used}/{cap}); "
                           f"hanh vi on_cap={policy.get('on_cap')}: can nguoi xac nhan"), warnings
        if used >= warn_at * cap:
            warnings.append(f"gan tran {cap_key}: {used}/{cap} (>={int(warn_at * 100)}%)")
    return True, f"duoc phep (mode={mode}, policy={policy.get('policy_version')})", warnings


def phase_of_task(t):
    """Anh xa plan task -> phase cua policy (don gian: build mac dinh)."""
    if t.get("role") in ("integrator", "error-analyst") or t.get("phase") == "build":
        return "integration"
    if t.get("mode") == "monitor":
        return "optimize"
    return "build"


def audit_path(run_dir):
    return os.path.join(run_dir, AUDIT_FILE)


def append_audit(run_dir, event, actor="person:coordinator", scope="", decision="",
                 reason="", refs=None, policy_version=None, extra=None):
    """Ghi 1 dong append-only vao audit.jsonl (qua statefile). Khong bao gio ghi de/sua lich su.

    Moi ban ghi co "id" UUID duy nhat: ID KG dan xuat tu audit id nen
    2 audit cung event/scope trong 1 phut khong tao canh lap, va sync lai
    cung ban ghi la idempotent (add_edge_checked dedup theo source/target/type).
    Dong bo KG loi -> ghi ban ghi "kg_sync_pending" truc tiep (khong de quy),
    KHONG lam hong audit.
    """
    if policy_version is None:
        p = load_policy(run_dir)
        policy_version = (p or {}).get("policy_version", "-")
    rec = {"id": uuid.uuid4().hex, "ts": utcnow(), "actor": actor, "event": event, "scope": scope,
           "policy_version": policy_version, "decision": decision,
           "reason": reason, "refs": refs or []}
    if extra:
        rec.update(extra)
    os.makedirs(os.path.abspath(run_dir), exist_ok=True)
    statefile_mod.append_jsonl(audit_path(run_dir), rec)
    try:
        audit_to_kg(run_dir, rec)
    except Exception as e:  # KG loi: ghi pending sync, audit van nguyen ven
        try:
            statefile_mod.append_jsonl(audit_path(run_dir), {
                "id": uuid.uuid4().hex, "ts": utcnow(), "actor": actor,
                "event": "kg_sync_pending", "scope": scope,
                "policy_version": policy_version, "decision": "pending",
                "reason": f"KG sync loi, can reconcile: {e}",
                "refs": [rec["id"]]})
        except Exception:
            pass
    return rec


# --- Noi audit -> KG (canh approved_by / decided_by, QUA API scripts/kg.py) ---

def audit_to_kg(run_dir, rec):
    """Noi event audit thanh canh KG qua upsert_entity/add_edge_checked.

    Chi emit khi knowledge/ ton tai; heartbeat va kg_sync_pending khong emit
    (tranh vong lap). Loi (KG fail, endpoint sai kieu...) duoc nem ra de
    append_audit ghi ban ghi kg_sync_pending; KHONG append thang vao
    entities/edges.jsonl (di qua validation + idempotency cua KG API).
    Khoa KG va audit KHONG long nhau: audit da commit (append_jsonl tra ve)
    truoc khi goi ham nay; KG API tu quan ly ghi cua no.
    """
    kd = os.path.join(os.path.abspath(run_dir), "knowledge")
    if not os.path.isdir(kd):
        return
    ev, scope = rec.get("event", ""), rec.get("scope", "")
    if ev in ("heartbeat", "kg_sync_pending"):
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import kg as kg_mod
    actor = rec.get("actor", "person:coordinator")
    ver = rec.get("policy_version", "-")
    aid = rec.get("id") or slug(rec.get("ts", ""))
    did = f"decision:{ev}-{slug(scope) or 'run'}-{aid[:12]}"
    pid = f"policy:autonomy-{slug(ver)}"
    person_id = actor if actor.startswith("person:") else f"person:{slug(actor)}"
    now = rec.get("ts", "")[:16].replace("T", " ")
    kg_mod.upsert_entity(run_dir, person_id, "Person", actor,
                         body=rec.get("reason", ""),
                         properties={"audit_ts": rec.get("ts"), "scope": scope},
                         created_at=now or None)
    kg_mod.upsert_entity(run_dir, did, "Decision",
                         f"{ev}: {scope} ({rec.get('decision', '')})",
                         body=rec.get("reason", ""),
                         properties={"audit_ts": rec.get("ts"), "scope": scope,
                                     "audit_id": rec.get("id")},
                         created_at=now or None)
    kg_mod.upsert_entity(run_dir, pid, "Policy", f"Autonomy policy {ver}",
                         body="", properties={}, created_at=now or None)
    edge_type = "approved_by" if ev in APPROVAL_EVENTS else "decided_by"
    kg_mod.add_edge_checked(run_dir, did, person_id, edge_type,
                            valid_from=now or None, recorded_at=now or None,
                            source_ref=AUDIT_FILE, confidence=1.0,
                            properties={"event": ev, "decision": rec.get("decision", ""),
                                        "audit_id": rec.get("id")})
    kg_mod.add_edge_checked(run_dir, did, pid, "uses",
                            valid_from=now or None, recorded_at=now or None,
                            source_ref=AUDIT_FILE, confidence=1.0,
                            properties={"event": ev, "audit_id": rec.get("id")})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    p_v = sp.add_parser("validate", help="Kiem tra policy JSON theo schema")
    p_v.add_argument("policy")

    p_c = sp.add_parser("check", help="Hoi policy co cho phep hanh dong khong (exit 0=duoc, 2=bi cam)")
    p_c.add_argument("run_dir")
    p_c.add_argument("--action", required=True, choices=ACTIONS)
    p_c.add_argument("--phase", choices=PHASES, help="phase cua task (mac dinh: default_mode)")
    p_c.add_argument("--task", help="id task (chi de hien thi/ghi log)")
    p_c.add_argument("--approved", action="store_true", help="da co approve cua nguoi cho scope nay")
    p_c.add_argument("--policy", help="duong dan policy (mac dinh runs/<id>/autonomy_policy.json)")
    p_c.add_argument("--usage-file", help="duong dan usage.json (mac dinh runs/<id>/usage.json)")

    p_a = sp.add_parser("approve", help="Nguoi duyet 1 scope + ghi audit (+ canh KG)")
    p_a.add_argument("run_dir")
    p_a.add_argument("--scope", required=True)
    p_a.add_argument("--decision", required=True, help="approve|reject")
    p_a.add_argument("--reason", required=True)
    p_a.add_argument("--actor", default="person:user")
    p_a.add_argument("--refs", default="")

    p_l = sp.add_parser("log", help="Ghi event audit tuy y (append-only)")
    p_l.add_argument("run_dir")
    p_l.add_argument("--event", required=True)
    p_l.add_argument("--scope", default="")
    p_l.add_argument("--decision", default="")
    p_l.add_argument("--reason", default="")
    p_l.add_argument("--actor", default="person:coordinator")
    p_l.add_argument("--refs", default="")

    a = ap.parse_args()
    if a.cmd == "validate":
        try:
            with open(a.policy, encoding="utf-8-sig") as f:
                p = json.load(f)
        except (OSError, ValueError) as e:
            sys.exit(f"policy error: khong doc duoc {a.policy}: {e}")
        errs = validate_policy(p)
        if errs:
            print("policy KHONG hop le:")
            for e in errs:
                print(f"  - {e}")
            return 1
        print(f"policy hop le: run={p['run_id']} version={p['policy_version']} default={p['default_mode']}")
        return 0

    if a.cmd == "check":
        status, pol, perrs = policy_status(a.run_dir, a.policy)
        if status == "missing":
            print("khong co autonomy_policy.json: cho phep (tuong thich nguoc run cu)")
            return 0
        if status in ("corrupt", "invalid"):
            sys.exit("policy error (fail-closed, dung truoc start): " + "; ".join(perrs))
        try:
            usage = read_usage_strict(a.run_dir, a.usage_file)
        except Exception as e:
            sys.exit(f"usage error (fail-closed): {e}")
        ok, reason, warns = check_action(pol, a.action, a.phase, usage, a.approved)
        for w in warns:
            print(f"canh bao: {w}")
        print(("CHO PHEP" if ok else "BI CAM") + f": {a.action} {a.task or ''} -> {reason}")
        return 0 if ok else 2

    refs = [r for r in a.refs.split(",") if r] if a.refs else []
    if a.cmd == "approve":
        rec = append_audit(a.run_dir, "approve", actor=a.actor, scope=a.scope,
                           decision=a.decision, reason=a.reason, refs=refs)
        print(f"da ghi approve: scope={a.scope} decision={a.decision} ts={rec['ts']}")
        return 0

    rec = append_audit(a.run_dir, a.event, actor=a.actor, scope=a.scope,
                       decision=a.decision, reason=a.reason, refs=refs)
    print(f"da ghi audit: event={a.event} scope={a.scope} ts={rec['ts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
