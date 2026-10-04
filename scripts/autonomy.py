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

API cho plan_to_orca / coordinator:
  from autonomy import load_policy, read_usage, check_action, append_audit

Stdlib only.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

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
    """Doc policy; None neu run chua co policy (tuong thich nguoc: run cu van chay)."""
    p = policy_path(run_dir, override)
    try:
        with open(p, encoding="utf-8-sig") as f:  # -sig: chiu duoc BOM do PowerShell ghi
            return json.load(f)
    except (OSError, ValueError):
        return None


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

    Phan biet 'chua biet' voi 0: file/khoa vang khong co nghia la da do duoc 0.
    check_action() bao 'usage unknown' cho cap tien/token/GPU khong co nguon do
    thay vi lang le cho qua.
    """
    p = override or os.path.join(run_dir, USAGE_FILE)
    try:
        with open(p, encoding="utf-8-sig") as f:  # -sig: chiu duoc BOM do PowerShell ghi
            u = json.load(f)
        return u if isinstance(u, dict) else {}
    except (OSError, ValueError):
        return {}


def write_usage(run_dir, usage, override=None):
    """Ghi usage.json kieu atomic (temp roi replace) de khong mat so lieu khi crash."""
    p = override or os.path.join(run_dir, USAGE_FILE)
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(usage, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)
    return usage


def reserve_task_quota(run_dir, n=1, usage=None, override=None):
    """Cong don tasks_started ngay vao usage.json ben vung (atomic).

    plan_to_orca --start-ready goi ham nay cho TUNG worker duoc duyet TRUOC KHI
    start worker do, nen vong check ke tiep trong cung wave thay so lieu moi
    (cap 1/wave 2 -> worker 2 bi tu choi). Crash giua reserve va start co the
    de lai quota da tru trong khi worker chua chay: coordinator tru lai
    (reserve -1) hoac sua usage.json thu cong truoc khi retry.
    """
    u = dict(usage) if usage is not None else read_usage(run_dir, override)
    u["tasks_started"] = (u.get("tasks_started") or 0) + n
    return write_usage(run_dir, u, override)


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
    """Ghi 1 dong append-only vao audit.jsonl. Khong bao gio ghi de/sua lich su."""
    if policy_version is None:
        p = load_policy(run_dir)
        policy_version = (p or {}).get("policy_version", "-")
    rec = {"ts": utcnow(), "actor": actor, "event": event, "scope": scope,
           "policy_version": policy_version, "decision": decision,
           "reason": reason, "refs": refs or []}
    if extra:
        rec.update(extra)
    os.makedirs(os.path.abspath(run_dir), exist_ok=True)
    with open(audit_path(run_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    audit_to_kg(run_dir, rec)
    return rec


# --- Noi audit -> KG (canh approved_by / decided_by, dung truc tiep file jsonl) ---

def _kg_paths(run_dir):
    d = os.path.join(os.path.abspath(run_dir), "knowledge")
    return os.path.join(d, "entities.jsonl"), os.path.join(d, "edges.jsonl")


def _kg_ids(run_dir):
    ids = {}
    p, _ = _kg_paths(run_dir)
    try:
        with open(p, encoding="utf-8-sig") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("id"):
                        ids[r["id"]] = r.get("type")
                except ValueError:
                    pass
    except OSError:
        pass
    return ids


def audit_to_kg(run_dir, rec):
    """Noi event audit thanh canh KG. Chi emit khi knowledge/ ton tai; khong bao gio fail."""
    try:
        kd = os.path.join(os.path.abspath(run_dir), "knowledge")
        if not os.path.isdir(kd):
            return
        ev, scope = rec.get("event", ""), rec.get("scope", "")
        if ev in ("heartbeat",):
            return
        actor = rec.get("actor", "person:coordinator")
        ver = rec.get("policy_version", "-")
        stamp = slug(rec.get("ts", "")[:16])
        did = f"decision:{ev}-{slug(scope) or 'run'}-{stamp}"
        pid = f"policy:autonomy-{slug(ver)}"
        ep, dp = _kg_paths(run_dir)
        have = _kg_ids(run_dir)
        now = rec.get("ts", "")[:16].replace("T", " ")

        def ensure(eid, etype, title):
            if eid not in have:
                with open(ep, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"id": eid, "type": etype, "title": title, "body": rec.get("reason", ""),
                                                "properties": {"audit_ts": rec.get("ts"), "scope": scope},
                                                "created_at": now}, ensure_ascii=False) + "\n")
                have[eid] = etype

        ensure(actor if actor.startswith("person:") else f"person:{slug(actor)}", "Person", actor)
        person_id = actor if actor.startswith("person:") else f"person:{slug(actor)}"
        ensure(did, "Decision", f"{ev}: {scope} ({rec.get('decision', '')})")
        ensure(pid, "Policy", f"Autonomy policy {ver}")
        edge_type = "approved_by" if ev in APPROVAL_EVENTS else "decided_by"
        with open(dp, "a", encoding="utf-8") as f:
            f.write(json.dumps({"source": did, "target": person_id, "type": edge_type,
                                        "valid_from": now or None, "valid_to": None,
                                        "recorded_at": now or None,
                                        "source_ref": AUDIT_FILE, "confidence": 1.0,
                                        "properties": {"event": ev, "decision": rec.get("decision", "")}},
                                       ensure_ascii=False) + "\n")
            f.write(json.dumps({"source": did, "target": pid, "type": "uses",
                                        "valid_from": now or None, "valid_to": None,
                                        "recorded_at": now or None,
                                        "source_ref": AUDIT_FILE, "confidence": 1.0,
                                        "properties": {"event": ev}}, ensure_ascii=False) + "\n")
    except OSError:
        pass


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
        pol = load_policy(a.run_dir, a.policy)
        if pol is None:
            print("khong co autonomy_policy.json: cho phep (tuong thich nguoc run cu)")
            return 0
        errs = validate_policy(pol)
        if errs:
            sys.exit("policy error: " + "; ".join(errs))
        usage = read_usage(a.run_dir, a.usage_file)
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
