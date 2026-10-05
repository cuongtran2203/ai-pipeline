#!/usr/bin/env python3
"""Bo dieu khien vong toi uu BAT BUOC sau baseline (optimize loop controller).

Chu trinh: baseline tren val/OOF -> `optimize.py next` -> DIAG hoac hanh dong
-> evaluate (val/OOF) + report + `optimize.py record` -> lap lai cho toi khi
`next` tra STOP. Khong bao gio phan tich loi hay lap tren split test: tap test
khoa chi cham mot lan o task cuoi `I-final` qua seal.

Commands:
  optimize.py init <run_dir> [--template T]
  optimize.py status <run_dir> [--json]
  optimize.py next <run_dir> [--json] [--apply]
  optimize.py record <run_dir> --round N

Dieu kien STOP (du 5):
  1. success   : moi target dat -> task cuoi `I-final` (test khoa mot lan qua seal) roi release.
  2. ask-human : ceiling (C1/C2) thap hon target, hoac OBJECTIVE/NOISE can nguoi.
  3. plateau   : cai thien < epsilon trong `patience` vong lien tiep.
  4. max_rounds: het so vong cho phep.
  5. budget    : het ngan sach (gpu_hours / so task).

Stdlib only. Da nen tang (utf-8, newline='\\n'). Moi ghi state/append qua
scripts/statefile.py (atomic, khoa). Ghi do thi tri thuc qua kg.py API da
kiem (upsert_entity / add_edge_checked). Ghi so thi nghiem qua notebook.py.
"""

import argparse
import datetime as dt
import glob
import json
import math
import os
import re
import sys

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statefile  # noqa: E402  (ghi state/append atomic + khoa)

try:
    import kg  # noqa: E402  (single validated write API cho knowledge graph)
except ImportError:  # pragma: no cover - scripts/ luon di kem
    kg = None

ALLOWED_ACTIONS = ("retrain", "postprocess", "add_module", "research_data",
                   "collect_data", "relabel")
ALLOWED_SPLITS = ("val", "oof")
VERDICTS = ("DATA", "MODEL", "STRUCTURE", "OBJECTIVE", "NOISE", "POSTPROCESS")


class OptimizeError(RuntimeError):
    """Loi cau hinh/du lieu: fail-closed (khong doan, khong ghi de)."""


# --- Duong dan ---

def policy_path(run_dir):
    return os.path.join(os.path.abspath(run_dir), "optimize_policy.json")


def optimize_dir(run_dir):
    return os.path.join(os.path.abspath(run_dir), "optimize")


def state_path(run_dir):
    return os.path.join(optimize_dir(run_dir), "state.json")


def rounds_path(run_dir):
    return os.path.join(optimize_dir(run_dir), "rounds.jsonl")


def ceiling_path(run_dir):
    return os.path.join(os.path.abspath(run_dir), "ceiling.json")


def plan_path(run_dir):
    return os.path.join(os.path.abspath(run_dir), "plan.json")


def agents_path(run_dir):
    return os.path.join(os.path.abspath(run_dir), "agents.json")


def reports_glob(run_dir):
    return os.path.join(os.path.abspath(run_dir), "reports", "round-*", "eval.json")


def diagnosis_glob(run_dir):
    return os.path.join(os.path.abspath(run_dir), "diagnosis", "*", "diagnosis.json")


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# --- Policy ---

def default_policy(run_id=""):
    return {
        "run_id": run_id,
        "policy_version": "v1",
        "updated_at": _now(),
        "approved_by": "person:user",
        "approved_at_G2": False,
        "max_rounds": 3,
        "epsilon": None,
        "patience": 2,
        "top_k": 2,
        "budget": {"gpu_hours": 8.0, "max_tasks": 10},
        "allowed_actions": list(ALLOWED_ACTIONS),
        "error_analysis_split": "val",
        "data_sources": [],
        "approved_sources": [],
        "weights": {},
        "targets": {},
    }


def load_policy(run_dir):
    """Tra (status, policy, errors). status: ok|missing|corrupt|invalid."""
    p = policy_path(run_dir)
    try:
        with open(p, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return ("missing", None,
                ["chua co optimize_policy.json (%s); chay `python scripts/optimize.py init <run_dir>` "
                 "roi trinh nguoi duyet o G2 cung playbook" % p])
    except OSError as e:
        return ("corrupt", None, ["khong doc duoc policy %s: %s" % (p, e)])
    if not text.strip():
        return ("corrupt", None,
                ["%s ton tai nhung rong; sua/xoa tay hoac khoi phuc ban sao, KHONG tu ghi de" % p])
    try:
        pol = json.loads(text)
    except ValueError as e:
        return ("corrupt", None, ["optimize_policy.json khong phai JSON hop le (%s)" % e])
    errs = validate_policy(pol)
    if errs:
        return ("invalid", pol, errs)
    return ("ok", pol, [])


def validate_policy(pol):
    """List loi (rong = hop le). Khong exit; CLI tu exit."""
    if not isinstance(pol, dict):
        return ["policy phai la object JSON"]
    errs = []
    for k in ("max_rounds", "patience", "budget", "allowed_actions",
              "error_analysis_split", "targets"):
        if k not in pol:
            errs.append("thieu truong bat buoc '%s'" % k)
    mr = pol.get("max_rounds")
    if mr is not None and (not isinstance(mr, int) or isinstance(mr, bool) or mr < 1):
        errs.append("max_rounds phai la so nguyen >= 1")
    pa = pol.get("patience")
    if pa is not None and (not isinstance(pa, int) or isinstance(pa, bool) or pa < 1):
        errs.append("patience phai la so nguyen >= 1")
    tk = pol.get("top_k", 2)
    if not isinstance(tk, int) or isinstance(tk, bool) or tk < 1:
        errs.append("top_k phai la so nguyen >= 1")
    ep = pol.get("epsilon")
    if ep is not None and (not isinstance(ep, (int, float)) or isinstance(ep, bool) or ep < 0):
        errs.append("epsilon phai la so >= 0 hoac null (null = theo sai so chuan bo danh gia)")
    bud = pol.get("budget")
    if bud is not None:
        if not isinstance(bud, dict):
            errs.append("budget phai la object {gpu_hours, max_tasks}")
        else:
            for bk in ("gpu_hours", "max_tasks"):
                v = bud.get(bk)
                if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0):
                    errs.append("budget.%s phai la so >= 0" % bk)
    aa = pol.get("allowed_actions")
    if aa is not None:
        if not isinstance(aa, list) or not aa:
            errs.append("allowed_actions phai la list khong rong")
        else:
            for a in aa:
                if a not in ALLOWED_ACTIONS:
                    errs.append("allowed_actions la '%s' (hop le: %s)" % (a, ", ".join(ALLOWED_ACTIONS)))
    split = pol.get("error_analysis_split")
    if split is not None and split not in ALLOWED_SPLITS:
        errs.append("error_analysis_split phai la 'val' hoac 'oof' "
                    "(TU CHOI split test: tap test khoa chi cham mot lan o I-final qua seal)")
    for key in ("data_sources", "approved_sources"):
        v = pol.get(key, [])
        if not isinstance(v, list):
            errs.append("%s phai la list" % key)
    w = pol.get("weights", {})
    if not isinstance(w, dict):
        errs.append("weights phai la object {field: trong_so}")
    else:
        for f, v in w.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                errs.append("weights.%s phai la so >= 0" % f)
    tg = pol.get("targets", {})
    if not isinstance(tg, dict):
        errs.append("targets phai la object {field: {metric, target}}")
    else:
        for f, t in tg.items():
            if not isinstance(t, dict) or "target" not in t:
                errs.append("targets.%s phai la object co 'target'" % f)
            elif not isinstance(t["target"], (int, float)) or isinstance(t["target"], bool):
                errs.append("targets.%s.target phai la so" % f)
    return errs


# --- Doc eval.json theo field ---

def _row_value(row):
    if isinstance(row.get("value"), (int, float)) and not isinstance(row.get("value"), bool):
        return float(row["value"])
    c, t = row.get("correct"), row.get("total")
    if isinstance(c, (int, float)) and isinstance(t, (int, float)) and t:
        return float(c) / float(t)
    return None


def _row_n(row):
    for k in ("total", "n", "denominator"):
        v = row.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return int(v)
    return None


def _norm_direction(raw, default="higher"):
    s = str(raw or default).lower()
    if "lower" in s:
        return "lower"
    return "higher"


def read_field_history(run_dir):
    """ Lich su metric theo field tu reports/round-*/eval.json (sap xep theo ten dir).

    Tra (fields, errors) voi fields[field] = [{round, value, n, split, eval_set}].
    Chi doc val/OOF: ban ghi nao khai split/test (split, eval_set) thi BO QUA
    (ky luat test: khong lap tren test).
    """
    paths = sorted(glob.glob(reports_glob(run_dir)))
    fields = {}
    skipped_test = []
    for p in paths:
        try:
            with open(p, encoding="utf-8-sig") as f:
                ev = json.load(f)
        except (OSError, ValueError):
            continue
        rnd = os.path.basename(os.path.dirname(p))
        for t in ev.get("tables", []) or []:
            for row in t.get("rows", []) or []:
                item = row.get("item")
                if not item:
                    continue
                split = str(row.get("split") or t.get("split") or "").lower()
                eset = str(row.get("eval_set") or t.get("eval_set") or "").lower()
                if "test" in split or "test" in eset:
                    skipped_test.append("%s:%s" % (rnd, item))
                    continue
                v = _row_value(row)
                if v is None:
                    continue
                fields.setdefault(str(item), []).append({
                    "round": rnd, "value": v, "n": _row_n(row),
                    "direction": _norm_direction(row.get("direction") or t.get("metric"),
                                                 "higher"),
                    "eval_file": p.replace(os.sep, "/"),
                })
    return fields, skipped_test


def std_error(value, n):
    """Sai so chuan nhi thuc sqrt(p(1-p)/n); None khi thieu n."""
    if n is None or n <= 0:
        return None
    p = min(max(value, 0.0), 1.0)
    return math.sqrt(p * (1.0 - p) / n)


def effective_epsilon(policy, value, n):
    """Epsilon hieu dung: policy.epsilon, hoac mac dinh theo sai so chuan
    bo danh gia (1/2 * SE) neu co n; cuoi cung 0.005."""
    if policy.get("epsilon") is not None:
        return float(policy["epsilon"])
    se = std_error(value, n)
    if se is not None:
        return 0.5 * se
    return 0.005


# --- Doc ceiling + diagnosis ---

def read_ceiling(run_dir):
    try:
        with open(ceiling_path(run_dir), encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def ceiling_upper_for(ceiling):
    """Upper gan nhat (uoc luong moi nhat); None khi khong co."""
    if not isinstance(ceiling, dict):
        return None
    ests = ceiling.get("estimates") or []
    if not ests:
        return None
    last = ests[-1]
    u = last.get("upper")
    return float(u) if isinstance(u, (int, float)) and not isinstance(u, bool) else None


def read_diagnoses(run_dir):
    """{component: diagnosis_dict} tu diagnosis/*/diagnosis.json (+ diagnosis.json cu)."""
    out = {}
    for p in sorted(glob.glob(diagnosis_glob(run_dir))):
        try:
            with open(p, encoding="utf-8-sig") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        comp = d.get("component") or os.path.basename(os.path.dirname(p))
        out[str(comp)] = d
    legacy = os.path.join(os.path.abspath(run_dir), "diagnosis.json")
    if os.path.isfile(legacy):
        try:
            with open(legacy, encoding="utf-8-sig") as f:
                d = json.load(f)
            items = d if isinstance(d, list) else [d]
            for e in items:
                if isinstance(e, dict) and e.get("component"):
                    out.setdefault(str(e["component"]), e)
        except (OSError, ValueError):
            pass
    return out


def norm_verdict(v):
    s = str(v or "").strip().upper()
    if s == "AUX":  # run cu dung ten AUX cho STRUCTURE
        return "STRUCTURE"
    return s if s in VERDICTS else ""


# --- State vong (optimize/state.json + rounds.jsonl) ---

def read_state(run_dir):
    st = statefile.read_json(state_path(run_dir), None)
    if not isinstance(st, dict):
        st = {}
    st.setdefault("next_round", 1)
    st.setdefault("pending_round", None)
    st.setdefault("rejected_branches", [])
    st.setdefault("calibration", [])
    return st


def write_state(run_dir, st):
    def fn(_old):
        return st
    statefile.update_json(state_path(run_dir), fn, default={})


def read_rounds(run_dir):
    try:
        rows = statefile.read_jsonl(rounds_path(run_dir), strict=True)
    except statefile.StateCorrupt as e:
        raise OptimizeError("rounds.jsonl hong: %s" % e)
    return [r for r in rows if isinstance(r, dict)]


def _improvement(new, old, direction):
    return (new - old) if direction == "higher" else (old - new)


def rounds_used(state, rounds):
    done = {r.get("round") for r in rounds if isinstance(r.get("round"), int)}
    if done:
        return max(max(done), int(state.get("next_round", 1)) - 1)
    return int(state.get("next_round", 1)) - 1


# --- Bang status ---

def build_status(run_dir):
    status, policy, errs = load_policy(run_dir)
    if status != "ok":
        raise OptimizeError("policy %s: %s" % (status, "; ".join(errs)))
    fields, skipped = read_field_history(run_dir)
    if not fields:
        raise OptimizeError("can chay baseline tren val/OOF truoc "
                            "(chua thay reports/round-*/eval.json nao doc duoc)")
    st = read_state(run_dir)
    rounds = read_rounds(run_dir)
    used = rounds_used(st, rounds)
    ceil = read_ceiling(run_dir)
    upper = ceiling_upper_for(ceil)
    rows = []
    for field in sorted(fields):
        hist = fields[field]
        base, latest = hist[0]["value"], hist[-1]["value"]
        n = hist[-1].get("n")
        direction = hist[-1].get("direction", "higher")
        tgt = (policy.get("targets", {}) or {}).get(field, {})
        target = tgt.get("target")
        eps = effective_epsilon(policy, latest, n)
        if target is None:
            gap, verdict = None, "chua dat target (thieu policy.targets.%s)" % field
        else:
            gap = _improvement(float(target), latest, direction)
            if gap <= 0:
                verdict = "dat"
            elif upper is not None and float(target) > upper and direction == "higher":
                verdict = "vuot ceiling: hoi nguoi"
            else:
                verdict = "chua dat"
        trend = latest - hist[-2]["value"] if len(hist) >= 2 else 0.0
        rows.append({
            "field": field, "baseline": base, "latest": latest, "n": n,
            "target": target, "gap": gap, "trend": trend,
            "rounds_used": used, "epsilon": eps, "verdict": verdict,
            "direction": direction,
        })
    return {"policy_version": policy.get("policy_version"), "rows": rows,
            "rounds_used": used, "skipped_test_tables": skipped}


def print_status(rep):
    print("=== Trang thai toi uu (moi field/component) ===")
    hdr = "%-22s %10s %10s %10s %10s %10s %6s %s" % (
        "field", "baseline", "moi nhat", "target", "khoang cach", "xu huong", "vong", "verdict")
    print(hdr)
    for r in rep["rows"]:
        tgt = "-" if r["target"] is None else ("%.4g" % r["target"])
        gap = "-" if r["gap"] is None else ("%.4g" % r["gap"])
        print("%-22s %10.4g %10.4g %10s %10s %+10.4g %6d %s" % (
            r["field"][:22], r["baseline"], r["latest"], tgt, gap,
            r["trend"], r["rounds_used"], r["verdict"]))
    if rep.get("skipped_test_tables"):
        print("(da bo qua %d bang khai split/test: ky luat test)" % len(rep["skipped_test_tables"]))


# --- Quyet dinh next ---

STOP_SUCCESS = "STOP-thanh-cong"
STOP_ASK = "STOP-hoi-nguoi"
STOP_PLATEAU = "STOP-het-hieu-qua-can-bien"
STOP_MAXROUNDS = "STOP-het-max_rounds"
STOP_BUDGET = "STOP-het-budget"


def _load_plan_tasks(run_dir):
    try:
        with open(plan_path(run_dir), encoding="utf-8-sig") as f:
            plan = json.load(f)
    except FileNotFoundError:
        return None, []
    except ValueError as e:
        raise OptimizeError("plan.json hong (%s); sua/xoa tay hoac khoi phuc ban sao" % e)
    tasks = plan.get("tasks", []) if isinstance(plan, dict) else []
    return plan, tasks


def _load_agents(run_dir):
    try:
        with open(agents_path(run_dir), encoding="utf-8-sig") as f:
            ag = json.load(f)
    except (OSError, ValueError):
        return None
    return ag if isinstance(ag, dict) else None


def _code_agent(ag):
    if isinstance(ag, dict):
        groups = ag.get("groups") or {}
        code = groups.get("code") or []
        if code:
            return code[0]
    return "auto"


def decide_next(run_dir):
    """Tra dict quyet dinh (chua ghi file). Loi cau hinh -> raise OptimizeError."""
    status, policy, errs = load_policy(run_dir)
    if status != "ok":
        raise OptimizeError("policy %s: %s" % (status, "; ".join(errs)))
    fields, _ = read_field_history(run_dir)
    if not fields:
        raise OptimizeError("can chay baseline tren val/OOF truoc "
                            "(chua thay reports/round-*/eval.json nao doc duoc)")
    st = read_state(run_dir)
    rounds = read_rounds(run_dir)
    used = rounds_used(st, rounds)
    diags = read_diagnoses(run_dir)
    ceil = read_ceiling(run_dir)
    upper = ceiling_upper_for(ceil)
    weights = policy.get("weights", {}) or {}
    targets = policy.get("targets", {}) or {}
    allowed = set(policy.get("allowed_actions") or [])

    latest_of = {f: h[-1] for f, h in fields.items()}
    unmet = []
    for f, h in latest_of.items():
        t = targets.get(f, {}).get("target")
        if t is None:
            unmet.append(f)
            continue
        if _improvement(float(t), h["value"], h.get("direction", "higher")) > 0:
            unmet.append(f)
    if not unmet:
        return {"decision": STOP_SUCCESS, "stop": True,
                "reason": "moi target da dat; task cuoi I-final (test khoa mot lan qua seal) roi release",
                "round": int(st.get("next_round", 1)), "tasks": [],
                "final_task": "I-final"}

    for f in unmet:
        t = targets.get(f, {}).get("target")
        d = diags.get(f, {})
        v = norm_verdict(d.get("verdict"))
        if v in ("OBJECTIVE", "NOISE"):
            return {"decision": STOP_ASK, "stop": True,
                    "reason": "field '%s' verdict %s can nguoi (doi metric/spec hoac ha target)" % (f, v),
                    "round": int(st.get("next_round", 1)), "tasks": [], "field": f}
        if (t is not None and upper is not None
                and _improvement(float(t), latest_of[f]["value"],
                                 latest_of[f].get("direction", "higher")) > 0
                and float(t) > upper and latest_of[f].get("direction", "higher") == "higher"):
            return {"decision": STOP_ASK, "stop": True,
                    "reason": "ceiling.json (C1/C2) upper=%.4g thap hon target %.4g cua '%s': hoi nguoi" % (
                        upper, float(t), f),
                    "round": int(st.get("next_round", 1)), "tasks": [], "field": f}

    max_rounds = int(policy.get("max_rounds", 3))
    if used >= max_rounds:
        return {"decision": STOP_MAXROUNDS, "stop": True,
                "reason": "da dung %d/%d vong" % (used, max_rounds),
                "round": int(st.get("next_round", 1)), "tasks": []}

    budget = policy.get("budget", {}) or {}
    _plan, ptasks = _load_plan_tasks(run_dir)
    r_tasks = [t for t in ptasks if isinstance(t, dict) and str(t.get("id", "")).startswith("R")]
    gpu_used = sum(float(r.get("gpu_hours", 0) or 0) for r in rounds)
    if budget.get("max_tasks") is not None and len(r_tasks) >= int(budget["max_tasks"]):
        return {"decision": STOP_BUDGET, "stop": True,
                "reason": "het budget so task (%d/%s)" % (len(r_tasks), budget["max_tasks"]),
                "round": int(st.get("next_round", 1)), "tasks": []}
    if budget.get("gpu_hours") is not None and gpu_used >= float(budget["gpu_hours"]):
        return {"decision": STOP_BUDGET, "stop": True,
                "reason": "het budget gpu_hours (%.2f/%s)" % (gpu_used, budget["gpu_hours"]),
                "round": int(st.get("next_round", 1)), "tasks": []}

    patience = int(policy.get("patience", 2))
    gains = []
    for r in sorted(rounds, key=lambda x: x.get("round", 0)):
        g = r.get("max_gain")
        if isinstance(g, (int, float)) and not isinstance(g, bool):
            gains.append(float(g))
    tail = gains[-patience:] if patience else []
    if len(tail) >= patience and patience > 0:
        eps_ref = 0.0
        if unmet:
            f0 = unmet[0]
            eps_ref = effective_epsilon(policy, latest_of[f0]["value"], latest_of[f0].get("n"))
        if all(g < eps_ref for g in tail):
            return {"decision": STOP_PLATEAU, "stop": True,
                    "reason": "cai thien %s < epsilon %.4g trong %d vong lien tiep" % (
                        "[" + ", ".join("%.4g" % g for g in tail) + "]", eps_ref, patience),
                    "round": int(st.get("next_round", 1)), "tasks": []}

    scored = []
    for f in unmet:
        t = targets.get(f, {}).get("target")
        gap = 1.0 if t is None else max(
            _improvement(float(t), latest_of[f]["value"], latest_of[f].get("direction", "higher")), 0.0)
        w = float(weights.get(f, 1.0))
        scored.append((gap * w, f))
    scored.sort(reverse=True)
    topk = int(policy.get("top_k", 2))
    chosen = [f for _, f in scored[:topk]]

    missing_diag = [f for f in chosen if f not in diags]
    rnd = int(st.get("next_round", 1))
    ag = _load_agents(run_dir)
    agent = _code_agent(ag)
    split = policy.get("error_analysis_split", "val")
    if missing_diag:
        tasks = [diag_task(rnd, f, agent, split) for f in missing_diag]
        return {"decision": "GO", "stop": False,
                "reason": "thieu diagnosis cho %s: sinh task DIAG truoc" % ", ".join(missing_diag),
                "round": rnd, "tasks": tasks}

    rejected = set()
    for r in rounds:
        for b in r.get("rejected_branches", []) or []:
            rejected.add(str(b))
    tasks = []
    for f in chosen:
        d = diags[f]
        v = norm_verdict(d.get("verdict")) or "MODEL"
        branch_tasks = tasks_for_verdict(rnd, f, latest_of[f], targets.get(f, {}),
                                         d, v, agent, split, policy, rejected)
        if branch_tasks is None:
            return {"decision": STOP_ASK, "stop": True,
                    "reason": "nhanh %s cho '%s' nam ngoai allowed_actions hoac can duyet nguon: hoi nguoi" % (v, f),
                    "round": rnd, "tasks": [], "field": f}
        tasks.extend(branch_tasks)
    if not tasks:
        return {"decision": STOP_ASK, "stop": True,
                "reason": "khong con nhanh hanh dong nao (cac nhanh da bi bac bo): hoi nguoi",
                "round": rnd, "tasks": []}
    tasks.append(eval_task(rnd, agent, split))
    chain_deps(tasks)
    return {"decision": "GO", "stop": False,
            "reason": "vong %d cho %s" % (rnd, ", ".join(chosen)),
            "round": rnd, "tasks": tasks}


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "x"


def diag_task(rnd, field, agent, split):
    comp = _slug(field)
    tid = "R%02d-%s-diag" % (rnd, comp)
    return {
        "id": tid,
        "title": "R%02d: chan doan diem yeu '%s' (weakness-diagnostician)" % (rnd, field),
        "role": "weakness-diagnostician",
        "agent": agent,
        "mode": "evaluate-only",
        "resources": {"compute": "cpu"},
        "worktree": "new-child",
        "deps": [],
        "owns": ["runs/<run_id>/diagnosis/%s/" % comp, "runs/<run_id>/artifacts/%s/" % tid],
        "inputs": ["reports/round-*/eval.json (val/OOF)", "runs/<id>/playbook.json"],
        "outputs": ["runs/<id>/diagnosis/%s/diagnosis.json" % comp],
        "target": "xac minh NGUYEN NHAN '%s' kem muc tieu (DATA/MODEL/STRUCTURE/OBJECTIVE/NOISE) bang thi nghiem phan biet re nhat" % field,
        "change": ("Chay thi nghiem phan biet tren split %s (KHONG dung test): Bobby hoc, "
                   "slice, oracle, overfit tap con, doc mu. Ghi diagnosis.json "
                   "(verdict + shares + actions co predicted_gain)." % split),
        "acceptance": "diagnosis.json co verdict thuoc DATA/MODEL/STRUCTURE/OBJECTIVE/NOISE + evidence moi test; predicted_gain=0.0 (chan doan, khong sua)",
        "predicted_gain": 0.0,
        "hypothesis": "chua ro nguyen nhan: can thi nghiem phan biet truoc khi sua",
        "measure": "diagnosis.json ton tai va hop le tren split %s" % split,
        "constraints": ["chi phan tich loi tren %s; cam split test" % split],
    }


def _need_external(d, policy):
    txt = json.dumps(d, ensure_ascii=False).lower()
    known = [str(s).lower() for s in (policy.get("data_sources") or [])]
    return ("research" in txt or "dataset" in txt or "du lieu ngoai" in txt
            or "thu thap" in txt or "collect" in txt or bool(known))


def _source_approved(d, policy):
    approved = {str(s).lower() for s in (policy.get("approved_sources") or [])}
    txt = json.dumps(d, ensure_ascii=False).lower()
    for src in approved:
        if src and src in txt:
            return True
    return not _need_external(d, policy) or not approved and False


def tasks_for_verdict(rnd, field, latest, target, diag, verdict, agent, split, policy, rejected):
    """List task cho 1 field theo nhanh da duyet; None = can hoi nguoi."""
    comp = _slug(field)
    allowed = set(policy.get("allowed_actions") or [])
    acts = diag.get("actions") or [{}]
    pred = 0.0
    for a in acts:
        try:
            pred = max(pred, float(a.get("predicted_gain", 0) or 0))
        except (TypeError, ValueError):
            pass
    if pred <= 0:
        gap = 1.0
        if target.get("target") is not None:
            gap = max(_improvement(float(target["target"]), latest["value"],
                                   latest.get("direction", "higher")), 0.001)
        pred = round(min(gap / 2.0, 0.05), 4) or 0.01
    base = {"agent": agent, "worktree": "new-child", "deps": [],
            "predicted_gain": pred,
            "hypothesis": "gia thuyet tu diagnosis %s (%s)" % (field, verdict),
            "split": split}
    branch_key = "%s:%s" % (field, verdict)
    if branch_key in rejected:
        return []
    out = []

    def own(tid, *extra):
        owns = ["runs/<run_id>/artifacts/%s/" % tid]
        owns.extend(extra)
        return owns

    if verdict == "DATA":
        if "research_data" not in allowed and _need_external(diag, policy):
            return None
        if _need_external(diag, policy):
            if not _source_approved(diag, policy):
                tid = "R%02d-%s-research" % (rnd, comp)
                out.append({**base, "id": tid,
                            "title": "R%02d: research dataset cong khai cho '%s'" % (rnd, field),
                            "role": "module-dev", "mode": "retrieve-only",
                            "resources": {"compute": "cpu"},
                            "owns": own(tid),
                            "inputs": ["diagnosis/%s/diagnosis.json" % comp],
                            "outputs": ["runs/<id>/artifacts/%s/nguon.md" % tid],
                            "target": "tim dataset cong khai phu hop '%s'" % field,
                            "change": ("Research dataset cong khai (skill ai-pipeline-research do task khac viet: "
                                       "chi tham chieu ten). KIEM TRA LECH PHAN BO bang thi nghiem nho truoc khi tin. "
                                       "CONG DUYET NGUON: chi tai sau khi nguoi duyet tuong nguon."),
                            "acceptance": "nguon.md co >=1 nguon + kiem chung lech phan bo tren val; predicted_gain=%.4g; chua tai khi chua duyet" % pred,
                            "measure": "nguon duoc nguoi duyet + do lech phan bo tren val",
                            "constraints": ["khong tai du lieu khi chua co duyet nguon"]})
        if "collect_data" in allowed or "relabel" in allowed:
            tid = "R%02d-%s-collect" % (rnd, comp)
            out.append({**base, "id": tid,
                        "title": "R%02d: thu thap/gan nhan bo sung '%s' (ask nguoi)" % (rnd, field),
                        "role": "module-dev", "mode": "evaluate-only",
                        "resources": {"compute": "cpu"},
                        "owns": own(tid),
                        "inputs": ["diagnosis/%s/diagnosis.json" % comp],
                        "outputs": ["runs/<id>/artifacts/%s/ds-vX.md" % tid],
                        "target": "bo sung du lieu that lat cat yeu '%s' (co version ds-vX)" % field,
                        "change": ("Xin du lieu that/lat cat yeu tu nguoi (ask), thu thap + gan version ds-vX; "
                                   "synth chi khi ablation tren val that cho thay khop."),
                        "acceptance": "ds-vX co version + ablation tren val that; predicted_gain=%.4g" % pred,
                        "measure": "so mau that moi co version + delta val",
                        "constraints": ["du lieu co version; synth phai ablation tren val that"]})
        if "retrain" not in allowed:
            return None
        tid = "R%02d-%s-retrain" % (rnd, comp)
        out.append({**base, "id": tid,
                    "title": "R%02d: retrain cai thien '%s' (DATA)" % (rnd, field),
                    "role": "module-dev", "mode": "train",
                    "resources": {"compute": "gpu"},
                    "owns": own(tid, "runs/<run_id>/modules/"),
                    "inputs": ["diagnosis/%s/diagnosis.json" % comp],
                    "outputs": ["runs/<id>/artifacts/%s/eval.json" % tid],
                    "target": "thu hep khoang cach '%s' tren %s" % (field, split),
                    "change": "Retrain voi du lieu bo sung (1 thay doi/vong); danh gia tren %s." % split,
                    "acceptance": "delta %s >= %.4g tren %s that; predicted_gain=%.4g" % (field, pred, split, pred),
                    "measure": "delta metric tren %s that" % split,
                    "constraints": ["can G3 truoc khi train; chi danh gia tren %s" % split]})
        return out

    if verdict == "MODEL":
        if "retrain" not in allowed:
            return None
        tid = "R%02d-%s-retrain" % (rnd, comp)
        out.append({**base, "id": tid,
                    "title": "R%02d: retrain bien the (do phan giai/quy mo/ngu canh) cho '%s'" % (rnd, field),
                    "role": "module-dev", "mode": "train",
                    "resources": {"compute": "gpu"},
                    "owns": own(tid, "runs/<run_id>/modules/"),
                    "inputs": ["diagnosis/%s/diagnosis.json" % comp],
                    "outputs": ["runs/<id>/artifacts/%s/eval.json" % tid],
                    "target": "thu hep khoang cach '%s' tren %s" % (field, split),
                    "change": ("Retrain 1 bien the (do phan giai/quy mo/ngu canh/schedule), "
                               "1 thay doi/vong; danh gia tren %s." % split),
                    "acceptance": "delta %s >= %.4g tren %s that; predicted_gain=%.4g" % (field, pred, split, pred),
                    "measure": "delta metric tren %s that" % split,
                    "constraints": ["can G3 truoc khi train; 1 thay doi/vong; chi danh gia tren %s" % split]})
        return out

    if verdict == "STRUCTURE":
        if "add_module" not in allowed:
            return None
        if _need_external(diag, policy):
            if "research_data" not in allowed:
                return None
            if not _source_approved(diag, policy):
                tid = "R%02d-%s-research" % (rnd, comp)
                out.append({**base, "id": tid,
                            "title": "R%02d: research dataset cong khai cho module phu '%s'" % (rnd, field),
                            "role": "module-dev", "mode": "retrieve-only",
                            "resources": {"compute": "cpu"},
                            "owns": own(tid),
                            "inputs": ["diagnosis/%s/diagnosis.json" % comp],
                            "outputs": ["runs/<id>/artifacts/%s/nguon.md" % tid],
                            "target": "tim dataset cong khai cho module phu cua '%s'" % field,
                            "change": ("Research dataset cong khai cho module phu (vd. classifier chu so viet tay). "
                                       "CONG DUYET NGUON: chi tai sau khi nguoi duyet."),
                            "acceptance": "nguon.md + duyet nguoi; predicted_gain=%.4g" % pred,
                            "measure": "nguon duoc duyet",
                            "constraints": ["khong tai du lieu khi chua co duyet nguon"]})
        tid = "R%02d-%s-aux" % (rnd, comp)
        out.append({**base, "id": tid,
                    "title": "R%02d: build module phu cho '%s'" % (rnd, field),
                    "role": "module-dev", "mode": "train",
                    "resources": {"compute": "gpu"},
                    "owns": own(tid, "runs/<run_id>/modules/%s-aux/" % comp),
                    "inputs": ["diagnosis/%s/diagnosis.json" % comp],
                    "outputs": ["runs/<id>/modules/%s-aux/eval.json" % comp],
                    "target": "module phu (phan loai/localiser/normaliser) cho '%s'" % field,
                    "change": ("Build module phu ma oracle da chung minh (vd. classifier chu so -> "
                               "dinh tuyen theo do tin cay voi nguong hieu chinh tren val)."),
                    "acceptance": "module phu do duoc tren %s + nguong hieu chinh tren val; predicted_gain=%.4g" % (split, pred),
                    "measure": "delta metric tren %s that" % split,
                    "constraints": ["can G3 truoc khi train; nguong dinh tuyen hieu chinh tren val"]})
        tid2 = "R%02d-%s-integrate" % (rnd, comp)
        out.append({**base, "id": tid2,
                    "title": "R%02d: tich hop + dinh tuyen module phu '%s'" % (rnd, field),
                    "role": "integrator", "mode": "evaluate-only",
                    "resources": {"compute": "cpu"},
                    "owns": own(tid2),
                    "inputs": ["modules/%s-aux/eval.json" % comp],
                    "outputs": ["runs/<id>/artifacts/%s/eval.json" % tid2],
                    "target": "dinh tuyen theo do tin cay vao field '%s'" % field,
                    "change": ("Tich hop module phu + dinh tuyen theo do tin cay (nguong hieu chinh tren val); "
                               "do lai e2e tren %s." % split),
                    "acceptance": "delta %s >= %.4g tren %s that; predicted_gain=%.4g" % (field, pred, split, pred),
                    "measure": "delta metric tren %s that" % split,
                    "constraints": ["chi danh gia tren %s" % split]})
        return out

    if verdict == "POSTPROCESS":
        if "postprocess" not in allowed:
            return None
        tid = "R%02d-%s-postprocess" % (rnd, comp)
        out.append({**base, "id": tid,
                    "title": "R%02d: hau xu ly luat cho '%s'" % (rnd, field),
                    "role": "integrator", "mode": "evaluate-only",
                    "resources": {"compute": "cpu"},
                    "owns": own(tid),
                    "inputs": ["diagnosis/%s/diagnosis.json" % comp],
                    "outputs": ["runs/<id>/artifacts/%s/eval.json" % tid],
                    "target": "sua cum loi bang luat cho '%s'" % field,
                    "change": "Them hau xu ly luat cho cum loi sua duoc (regex/chuan hoa); do lai tren %s." % split,
                    "acceptance": "delta %s >= %.4g tren %s that; predicted_gain=%.4g" % (field, pred, split, pred),
                    "measure": "delta metric tren %s that" % split,
                    "constraints": ["chi danh gia tren %s" % split]})
        return out

    return None


def eval_task(rnd, agent, split):
    tid = "R%02d-eval" % rnd
    return {
        "id": tid,
        "title": "R%02d: danh gia val/OOF + report + record" % rnd,
        "role": "integrator",
        "agent": agent,
        "mode": "evaluate-only",
        "resources": {"compute": "cpu"},
        "worktree": "current",
        "deps": [],
        "owns": ["runs/<run_id>/reports/round-%02d/" % rnd],
        "inputs": ["runs/<id>/artifacts/R%02d-*/" % rnd],
        "outputs": ["runs/<id>/reports/round-%02d/eval.json" % rnd,
                    "runs/<id>/reports/round-%02d/report.md" % rnd,
                    "runs/<id>/reports/round-%02d/report.html" % rnd],
        "target": "do lai e2e tren %s that + bao cao" % split,
        "change": ("Danh gia e2e tren %s that (KHONG dung test) -> eval.json; "
                   "render report.md + report.html (render_report); "
                   "chay `python scripts/optimize.py record <run_dir> --round %d`." % (split, rnd)),
        "acceptance": "eval.json + report.md + report.html ton tai; rounds.jsonl co ban ghi round %d; predicted_gain=0.0 (do luong)" % rnd,
        "predicted_gain": 0.0,
        "hypothesis": "do luong vong %d (khong phai gia thuyet cai thien)" % rnd,
        "measure": "eval.json tren %s that + record round %d" % (split, rnd),
        "constraints": ["chi danh gia tren %s; cam split test" % split],
    }


def chain_deps(tasks):
    prev = None
    for t in tasks:
        if prev is not None:
            deps = list(t.get("deps") or [])
            if prev not in deps:
                deps.insert(0, prev)
            t["deps"] = deps
        prev = t["id"]
    return tasks


def final_task():
    return {
        "id": "I-final",
        "title": "I-final: danh gia test khoa mot lan qua seal + release",
        "role": "integrator",
        "agent": "auto",
        "mode": "evaluate-only",
        "resources": {"compute": "cpu"},
        "worktree": "current",
        "deps": [],
        "owns": ["runs/<run_id>/reports/final/"],
        "inputs": ["runs/<id>/reports/round-*/eval.json"],
        "outputs": ["runs/<id>/reports/final/eval.json"],
        "target": "cham test khoa mot lan duy nhat sau khi recipe da khoa",
        "change": ("Xin grant test qua `python scripts/seal.py grant` (integrator, mot luot), "
                   "cham e2e, dong goi release theo phuong thuc G2 da duyet."),
        "acceptance": "eval test mot lan (seal) + bao cao cuoi; khong mo lai test",
        "predicted_gain": 0.0,
        "hypothesis": "recipe da khoa: xac nhan mot lan tren test khoa",
        "measure": "eval.json final tren test khoa (1 lan)",
        "constraints": ["test khoa chi cham mot lan qua seal; mo lai bi danh dau exploratory"],
    }


# --- Apply vao plan.json (idempotent) ---

def apply_next(run_dir, decision):
    """Ghi vong vao plan.json. Idempotent: chay 2 lan khong nhan doi.
    Tu choi neu vong truoc chua `record` (pending_round)."""
    st = read_state(run_dir)
    if st.get("pending_round") is not None:
        raise OptimizeError("vong %s chua `record` (pending_round=%s): chay "
                            "`python scripts/optimize.py record <run_dir> --round %s` truoc" % (
                                st["pending_round"], st["pending_round"], st["pending_round"]))
    plan_p = plan_path(run_dir)
    try:
        with open(plan_p, encoding="utf-8-sig") as f:
            plan = json.load(f)
    except FileNotFoundError:
        raise OptimizeError("chua co plan.json (%s)" % plan_p)
    except ValueError as e:
        raise OptimizeError("plan.json hong (%s)" % e)
    if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list):
        raise OptimizeError("plan.json phai la object co list 'tasks'")
    tasks = plan["tasks"]
    by_id = {t.get("id"): t for t in tasks if isinstance(t, dict)}
    new_tasks = list(decision.get("tasks") or [])
    if decision.get("decision") == STOP_SUCCESS:
        new_tasks = [final_task()]
        new_tasks[0]["agent"] = _code_agent(_load_agents(run_dir))
    if not new_tasks:
        return {"applied": 0, "round": decision.get("round"), "note": "STOP: khong ghi task moi"}
    gate_deps = [g for g in ("G2", "G3") if g in by_id]
    added = []
    for t in new_tasks:
        if t["id"] in by_id:
            continue
        deps = list(t.get("deps") or [])
        if not deps and gate_deps:
            need_g3 = t.get("mode") == "train" or (t.get("resources") or {}).get("compute") == "gpu"
            t["deps"] = (["G2"] if "G2" in gate_deps else []) + (["G3"] if need_g3 and "G3" in gate_deps else [])
        ag = _load_agents(run_dir)
        if t.get("agent") in (None, "auto") and ag:
            t["agent"] = _code_agent(ag)
        tasks.append(t)
        by_id[t["id"]] = t
        added.append(t["id"])

    def fn(_old):
        return plan
    statefile.update_json(plan_p, fn, default={})
    if added:
        st["pending_round"] = int(decision.get("round", st.get("next_round", 1)))
        write_state(run_dir, st)
    return {"applied": len(added), "round": decision.get("round"),
            "task_ids": added,
            "note": "idempotent: %d task moi (%d da ton tai duoc giu nguyen)" % (
                len(added), len(new_tasks) - len(added))}


# --- Record vong ---

def _round_report_dirs(run_dir):
    return sorted(glob.glob(os.path.join(os.path.abspath(run_dir), "reports", "round-*")))


def _read_eval_file(p):
    with open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def _eval_values(ev):
    vals = {}
    for t in ev.get("tables", []) or []:
        for row in t.get("rows", []) or []:
            if row.get("item") and _row_value(row) is not None:
                vals[str(row["item"])] = {
                    "value": _row_value(row), "n": _row_n(row),
                    "direction": _norm_direction(row.get("direction"), "higher")}
    return vals


def _predicted_for_round(plan_tasks, rnd):
    preds = {}
    for t in plan_tasks or []:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id", ""))
        m = re.match(r"R(\d+)-(.+)-(retrain|postprocess|aux|integrate|research|collect|diag|eval)$", tid)
        if not m or int(m.group(1)) != rnd:
            continue
        try:
            preds[tid] = float(t.get("predicted_gain", 0) or 0)
        except (TypeError, ValueError):
            preds[tid] = 0.0
        acc = str(t.get("acceptance", ""))
        mp = re.search(r"predicted_gain\s*=\s*([0-9.]+)", acc)
        if mp:
            try:
                preds[tid] = max(preds[tid], float(mp.group(1)))
            except ValueError:
                pass
    return preds


def record_round(run_dir, rnd):
    """Doc eval moi, ghi rounds.jsonl (append-only), cap nhat state, KG, so."""
    status, policy, errs = load_policy(run_dir)
    if status != "ok":
        raise OptimizeError("policy %s: %s" % (status, "; ".join(errs)))
    dirs = _round_report_dirs(run_dir)
    if not dirs:
        raise OptimizeError("chua co reports/round-*/eval.json de record vong %d" % rnd)
    newest = dirs[-1]
    ev_path = os.path.join(newest, "eval.json")
    if not os.path.isfile(ev_path):
        raise OptimizeError("thieu eval.json o %s" % newest)
    try:
        new_ev = _read_eval_file(ev_path)
    except ValueError as e:
        raise OptimizeError("eval.json hong (%s)" % e)
    new_vals = _eval_values(new_ev)
    if len(dirs) >= 2 and os.path.isfile(os.path.join(dirs[-2], "eval.json")):
        try:
            old_vals = _eval_values(_read_eval_file(os.path.join(dirs[-2], "eval.json")))
        except ValueError:
            old_vals = {}
    else:
        fields, _ = read_field_history(run_dir)
        old_vals = {f: {"value": h[0]["value"], "n": h[0].get("n"),
                        "direction": h[0].get("direction", "higher")}
                    for f, h in fields.items() if len(h) >= 1
                    and os.path.abspath(h[0].get("eval_file", "")) != os.path.abspath(ev_path)}
        if not old_vals:
            old_vals = {}
    _plan, ptasks = _load_plan_tasks(run_dir)
    preds = _predicted_for_round(ptasks, rnd)
    pred_gain = max(preds.values()) if preds else 0.0
    per_field, gains = {}, []
    for f, nv in new_vals.items():
        ov = old_vals.get(f)
        before = ov["value"] if ov else None
        direction = nv.get("direction", "higher")
        mg = _improvement(nv["value"], before, direction) if before is not None else 0.0
        gains.append(abs(mg))
        per_field[f] = {"before": before, "after": nv["value"],
                        "measured_gain": mg, "n": nv.get("n")}
    max_gain = max(gains) if gains else 0.0
    eps_ref = 0.0
    if new_vals:
        f0 = sorted(new_vals)[0]
        eps_ref = effective_epsilon(policy, new_vals[f0]["value"], new_vals[f0].get("n"))
    verdict = "giu" if max_gain >= eps_ref else "bo"
    rejected = []
    if verdict == "bo":
        diags = read_diagnoses(run_dir)
        for f in per_field:
            v = norm_verdict((diags.get(f) or {}).get("verdict"))
            if v:
                rejected.append("%s:%s" % (f, v))
    calib = None
    if pred_gain > 0:
        calib = max_gain / pred_gain
    rec = {"round": rnd, "report": os.path.basename(newest),
           "ts": _now(), "per_field": per_field,
           "predicted_gain": pred_gain, "measured_gain": max_gain,
           "max_gain": max_gain, "calibration": calib,
           "verdict": verdict, "rejected_branches": rejected,
           "gpu_hours": 0.0}
    statefile.append_jsonl(rounds_path(run_dir), rec)
    st = read_state(run_dir)
    if int(st.get("next_round", 1)) <= rnd:
        st["next_round"] = rnd + 1
    if st.get("pending_round") == rnd:
        st["pending_round"] = None
    rej = set(st.get("rejected_branches", []) or [])
    for b in rejected:
        rej.add(b)
    st["rejected_branches"] = sorted(rej)
    if calib is not None:
        cal = list(st.get("calibration", []) or [])
        cal.append({"round": rnd, "ratio": calib})
        st["calibration"] = cal[-10:]
    write_state(run_dir, st)
    _sync_record_kg(run_dir, rnd, rec, new_ev)
    _log_record_notebook(run_dir, rnd, rec)
    return rec


def _sync_record_kg(run_dir, rnd, rec, new_ev):
    if kg is None:
        return
    ts = _now()
    try:
        exp_id = "exp:optimize-R%02d" % rnd
        kg.upsert_entity(run_dir, exp_id, "Experiment",
                         "Vong toi uu R%02d (%s)" % (rnd, rec["verdict"]),
                         body=json.dumps(rec.get("per_field", {}), ensure_ascii=False),
                         properties={"round": rnd, "verdict": rec["verdict"],
                                     "predicted_gain": rec.get("predicted_gain"),
                                     "measured_gain": rec.get("measured_gain")},
                         created_at=ts)
        dec_id = "decision:optimize-R%02d" % rnd
        kg.upsert_entity(run_dir, dec_id, "Decision",
                         "Quyet dinh vong R%02d: %s" % (rnd, rec["verdict"]),
                         body="predicted=%.4g measured=%.4g verdict=%s" % (
                             rec.get("predicted_gain") or 0, rec.get("measured_gain") or 0,
                             rec["verdict"]),
                         created_at=ts)
        kg.add_edge_checked(run_dir, dec_id, exp_id, "evidenced_by",
                            valid_from=ts, recorded_at=ts,
                            source_ref="optimize/rounds.jsonl")
    except Exception as e:  # noqa: BLE001 - KG loi khong lam hong record
        print("  [CANH BAO] khong ghi duoc KG (%s)" % e, file=sys.stderr)


def _log_record_notebook(run_dir, rnd, rec):
    try:
        import notebook  # noqa: E402
        ns = argparse.Namespace(
            run_dir=run_dir, type="experiment",
            title="Vong toi uu R%02d: %s" % (rnd, rec["verdict"]),
            body=("predicted_gain=%.4g measured_gain=%.4g verdict=%s. %s" % (
                rec.get("predicted_gain") or 0, rec.get("measured_gain") or 0,
                rec["verdict"], json.dumps(rec.get("per_field", {}), ensure_ascii=False))),
            tags="optimize,R%02d" % rnd,
            metrics=json.dumps({"measured_gain": rec.get("measured_gain"),
                                "predicted_gain": rec.get("predicted_gain")}),
            refs="optimize/rounds.jsonl", author="optimize",
            no_kg=True, kg_edges=None)
        notebook.cmd_log(ns)
    except Exception as e:  # noqa: BLE001
        print("  [CANH BAO] khong ghi duoc so thi nghiem (%s)" % e, file=sys.stderr)


# --- CLI ---

def cmd_init(a):
    tpl = a.template
    if tpl is None:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tpl = os.path.join(here, "templates", "optimize_policy.template.json")
    try:
        with open(tpl, encoding="utf-8-sig") as f:
            pol = json.load(f)
    except (OSError, ValueError) as e:
        print("LOI: khong doc duoc template %s (%s)" % (tpl, e), file=sys.stderr)
        return 1
    run_id = os.path.basename(os.path.abspath(a.run_dir))
    if not pol.get("run_id") or pol.get("run_id", "").startswith("<"):
        pol["run_id"] = run_id
    p = policy_path(a.run_dir)
    if os.path.exists(p):
        print("da co %s (khong ghi de; trinh nguoi duyet o G2 cung playbook)" % p)
        return 0
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(pol, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.makedirs(optimize_dir(a.run_dir), exist_ok=True)
    print("da tao %s (nguoi duyet o G2 cung playbook)" % p)
    return 0


def cmd_status(a):
    try:
        rep = build_status(a.run_dir)
    except OptimizeError as e:
        print("LOI: %s" % e, file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print_status(rep)
    return 0


def cmd_next(a):
    try:
        dec = decide_next(a.run_dir)
    except OptimizeError as e:
        print("LOI: %s" % e, file=sys.stderr)
        return 2
    applied = None
    if a.apply:
        if dec.get("stop") and dec.get("decision") != STOP_SUCCESS:
            applied = {"applied": 0, "note": "STOP: khong ghi task moi"}
        else:
            try:
                applied = apply_next(a.run_dir, dec)
            except OptimizeError as e:
                print("LOI: %s" % e, file=sys.stderr)
                return 2
    if a.json:
        out = dict(dec)
        if applied is not None:
            out["apply"] = applied
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("=== Vong toi uu ke tiep ===")
        print("Quyet dinh: %s" % dec["decision"])
        print("Ly do: %s" % dec["reason"])
        for t in dec.get("tasks", []):
            print("  - %s [%s|%s] %s (predicted_gain=%s)" % (
                t["id"], t.get("role"), t.get("mode"), t["title"], t.get("predicted_gain")))
        if applied is not None:
            print("Apply: %s" % json.dumps(applied, ensure_ascii=False))
        if dec.get("stop"):
            print("Ket qua: DUNG (khong sinh vong moi).")
        else:
            print("Ket qua: CHAY vong %d (ghi vao plan.json khi --apply, chay bang plan_to_orca.py nhu thuong)." % dec["round"])
    return 0


def cmd_record(a):
    try:
        rec = record_round(a.run_dir, a.round)
    except OptimizeError as e:
        print("LOI: %s" % e, file=sys.stderr)
        return 2
    print("da record vong %d: measured_gain=%.4g verdict=%s (rounds.jsonl append-only)" % (
        a.round, rec.get("measured_gain") or 0, rec["verdict"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p_init = sp.add_parser("init", help="Tao optimize_policy.json tu template")
    p_init.add_argument("run_dir")
    p_init.add_argument("--template")
    p_status = sp.add_parser("status", help="Bang tieng Viet moi field/component")
    p_status.add_argument("run_dir")
    p_status.add_argument("--json", action="store_true")
    p_next = sp.add_parser("next", help="Quyet dinh buoc ke tiep (STOP/GO)")
    p_next.add_argument("run_dir")
    p_next.add_argument("--json", action="store_true")
    p_next.add_argument("--apply", action="store_true",
                        help="Ghi vong vao plan.json (idempotent; tu choi neu vong truoc chua record)")
    p_rec = sp.add_parser("record", help="Ghi nhan ket qua vong N")
    p_rec.add_argument("run_dir")
    p_rec.add_argument("--round", type=int, required=True)
    a = ap.parse_args(argv)
    handlers = {"init": cmd_init, "status": cmd_status,
                "next": cmd_next, "record": cmd_record}
    return handlers[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
