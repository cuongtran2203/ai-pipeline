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
import hashlib
import json
import math
import os
import re
import sys
import unicodedata

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
        # Nguon metric khai bao (OP3): null = suy doan cu chi khi file don gian
        # (1 bang, khong co eval_contract, khong dau hieu ablation); nhieu bang hoac
        # hang ablation ma khong co khai bao -> LOI fail-closed, khong doan.
        "metrics_source": None,
        # Target mac dinh cho moi field chua co targets.<field> (vd. 0.99 trich tu
        # spec "moi field >= 99%"); null = thieu target -> STOP-hoi-nguoi.
        "default_target": None,
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
    ms = pol.get("metrics_source")
    if ms is not None:
        errs.extend(validate_metrics_source(ms))
    dft = pol.get("default_target")
    if dft is not None and (not isinstance(dft, (int, float)) or isinstance(dft, bool)):
        errs.append("default_target phai la so hoac null "
                    "(target mac dinh cho moi field chua co targets.<field>)")
    return errs


def validate_metrics_source(ms):
    """List loi cua policy.metrics_source (rong = hop le)."""
    if not isinstance(ms, dict):
        return ["metrics_source phai la object "
                "{table_title_regex|table_index, field_column, value_column, "
                "field_regex?, exclude_regex?} hoac null"]
    errs = []
    has_regex = ms.get("table_title_regex")
    has_index = ms.get("table_index")
    if not has_regex and has_index is None:
        errs.append("metrics_source can it nhat mot trong "
                    "table_title_regex | table_index")
    if has_regex is not None:
        if not isinstance(has_regex, str) or not has_regex:
            errs.append("metrics_source.table_title_regex phai la chuoi regex khong rong")
        else:
            try:
                re.compile(has_regex)
            except re.error as e:
                errs.append("metrics_source.table_title_regex khong hop le (%s)" % e)
    if has_index is not None:
        if not isinstance(has_index, int) or isinstance(has_index, bool) or has_index < 0:
            errs.append("metrics_source.table_index phai la so nguyen >= 0")
    for key in ("field_column", "value_column"):
        v = ms.get(key)
        if v is not None and (not isinstance(v, str) or not v):
            errs.append("metrics_source.%s phai la chuoi ten cot (hoac de null/auto)" % key)
    for key in ("field_regex", "exclude_regex"):
        v = ms.get(key)
        if v is not None:
            if not isinstance(v, str) or not v:
                errs.append("metrics_source.%s phai la chuoi regex khong rong" % key)
            else:
                try:
                    re.compile(v)
                except re.error as e:
                    errs.append("metrics_source.%s khong hop le (%s)" % (key, e))
    return errs


# --- Doc eval.json theo field (nguon metric khai bao, OP3) ---
#
# Quy tac chon hang metric (fail-closed, khong doan):
#   (a) policy.metrics_source co khai bao -> chi dung bang/hang khop khai bao.
#   (b) eval.json co eval_contract (schema v2) -> tin toan bo hang (ban danh gia
#       co hop dong: tables = scope, rows = metric/thanh phan da chot).
#   (c) file don gian kieu cu (1 bang, khong co dau hieu ablation/so sanh bien
#       the) -> giu hanh vi cu de tuong thich nguoc.
#   Khong roi vao (a)/(b)/(c) (vd. nhieu bang ma khong khai bao, hang ablation
#   "RPA +P +augment ... hieu -3.9d CI95 ... -> HAI") -> LOI ro rang, goi y chay
#   `init` de liet ke bang ung vien. Parser cu coi moi hang la mot component
#   nen sinh hang chuc component rac + task DIAG id dai ca cau.

# Dau hieu hang/bang ablation (chi de PHAT HIEN mo ho, khong de tu chon).
_ABLATION_MARKERS = ("hieu ", "ci95", "ci 95", "->", "-->", "so voi", "so sanh",
                     "bien the", "ablation", "+augment", "(p,a)", "(c,a)", "(p,",
                     "(a)", "(c)", "(p)", "baseline_correct", "seed", "% train")

_MIDDLE_DOT = "\u00b7"


def _looks_like_variant_row(item):
    s = str(item or "").lower()
    if _MIDDLE_DOT in s:
        return True
    return sum(1 for m in _ABLATION_MARKERS if m in s) >= 1 and (
        "hieu " in s or "ci95" in s or _MIDDLE_DOT in s or "->" in s
        or "so voi" in s or "ablation" in s)


def _looks_like_ablation_table(table):
    rows = table.get("rows", []) or []
    if not rows:
        return False
    hits = sum(1 for r in rows
               if isinstance(r, dict) and _looks_like_variant_row(r.get("item")))
    return hits >= 2 and hits * 2 >= len(rows)


def _is_total_key(key):
    name = key.split("/")[-1].strip().lower() if "/" in key else key.strip().lower()
    return name == "all"

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


def _row_ci_eps(row, value):
    """Epsilon tu cot CI/sai so chuan neu co: CI95 rong w -> SE ~= w/4,
    epsilon mac dinh = SE/2 -> w/8. Tra None khi khong trich duoc."""
    raw = row.get("ci")
    if raw is None:
        for k in ("se", "std_err", "stderr", "sai_so"):
            v = row.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                return 0.5 * float(v)
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return 0.5 * float(raw)
    s = str(raw).replace(",", ".")
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", s)]
    if len(nums) >= 2:
        a, b = nums[0], nums[1]
        if value is not None and value <= 1.0 and max(abs(a), abs(b)) > 1.0 \
                and max(abs(a), abs(b)) <= 100.0:
            a, b = a / 100.0, b / 100.0
        w = abs(b - a)
        if 0 < w <= 1.0:
            return w / 8.0
        return None
    if len(nums) == 1 and 0 < nums[0] < 0.5:
        return 0.5 * nums[0]
    return None


def _norm_direction(raw, default="higher"):
    s = str(raw or default).lower()
    if "lower" in s:
        return "lower"
    return "higher"


def _eval_files_by_round(run_dir):
    """{round_label: [file, ...]} tu reports/round-*/eval.json (sap xep duong dan).
    Nhieu eval.json cung vong -> lay ban moi nhat (duong dan cuoi)."""
    out = {}
    for p in sorted(glob.glob(reports_glob(run_dir))):
        rnd = os.path.basename(os.path.dirname(p))
        out.setdefault(rnd, []).append(p)
    return out


def _load_eval_file(p):
    with open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def _table_matches_source(table, idx, ms):
    ti = ms.get("table_index")
    if ti is not None and idx != int(ti):
        return False
    rx = ms.get("table_title_regex")
    if rx and not re.search(rx, str(table.get("name") or "")):
        return False
    return True


def _extract_field_key(raw_item, ms):
    """Khoa field tu mot hang duoi metrics_source.
    Mac dinh: lay phan truoc dau '·' (tien to "nhom/ten" nhu "hw/date"),
    chuan hoa "nhom / ten" -> "nhom/ten". field_regex (neu co) trich truoc."""
    raw = str(raw_item or "")
    if ms.get("field_regex"):
        m = re.search(ms["field_regex"], raw)
        if not m:
            return None
        key = m.group(1) if m.groups() else m.group(0)
    elif _MIDDLE_DOT in raw:
        key = raw.split(_MIDDLE_DOT)[0]
    else:
        key = raw
    key = key.strip()
    if "/" in key:
        grp, _, name = key.partition("/")
        key = "%s/%s" % (grp.strip(), name.strip())
    return key or None


def _resolve_file_metrics(ev, policy, source_kind):
    """Gop cac hang cua MOT eval.json thanh {field: entry} (1 entry/field/file).
    entry = {value, n, direction, ci_eps, is_total}.
    Nhieu hang cung field (bang ablation: RC/RA/RCA...) -> neu tat ca chung mot
    baseline (baseline_correct/total) thi lay baseline (= he thong hien tai);
    neu chung mot correct/total thi lay gia tri do; con lai lay hang dau + canh bao.
    Hang tong 'ALL' giu de bao cao (is_total=True), khong bao gio la component sua."""
    ms = policy.get("metrics_source") if source_kind == "declared" else None
    field_col = (ms.get("field_column") if ms and ms.get("field_column")
                 else "item")
    value_col = (ms.get("value_column") if ms and ms.get("value_column")
                 else "auto")
    tables = ev.get("tables", []) or []
    if source_kind == "declared":
        picked = [(i, t) for i, t in enumerate(tables)
                  if isinstance(t, dict) and _table_matches_source(t, i, ms)]
        if not picked:
            names = [str((t or {}).get("name") or "?") for t in tables]
            raise OptimizeError(
                "metrics_source khong khop bang nao (table_title_regex=%r, "
                "table_index=%r; cac bang hien co: %s)" % (
                    ms.get("table_title_regex"), ms.get("table_index"),
                    "; ".join("[%d] %s" % (i, n) for i, n in enumerate(names))))
        tables = [t for _, t in picked]
    grouped = {}
    order = []
    skipped_test = []
    for t in tables:
        if not isinstance(t, dict):
            continue
        for row in t.get("rows", []) or []:
            if not isinstance(row, dict):
                continue
            raw_item = row.get(field_col, row.get("item"))
            if not raw_item:
                continue
            if ms and ms.get("exclude_regex") and re.search(
                    ms["exclude_regex"], str(raw_item)):
                continue
            split = str(row.get("split") or t.get("split") or "").lower()
            eset = str(row.get("eval_set") or t.get("eval_set") or "").lower()
            if "test" in split or "test" in eset:
                skipped_test.append(str(raw_item)[:60])
                continue
            if source_kind == "declared":
                key = _extract_field_key(raw_item, ms)
                if not key:
                    continue
            else:
                key = str(raw_item).strip()
                if not key:
                    continue
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append((row, t))
    out = {}
    warnings = []
    for key in order:
        pairs = grouped[key]
        vals = []
        for row, t in pairs:
            v = None
            if value_col != "auto":
                rv = row.get(value_col)
                if isinstance(rv, (int, float)) and not isinstance(rv, bool):
                    v = float(rv)
            else:
                v = _row_value(row)
            if v is None:
                continue
            vals.append((v, row, t))
        if not vals:
            continue
        base_set = set()
        cur_set = set()
        for v, row, t in vals:
            n = _row_n(row)
            bc = row.get("baseline_correct")
            if isinstance(bc, (int, float)) and not isinstance(bc, bool) and n:
                base_set.add((float(bc) / n, n))
            c = row.get("correct")
            if isinstance(c, (int, float)) and not isinstance(c, bool) and n:
                cur_set.add((float(c) / n, n))
        if len(vals) > 1 and len(base_set) == 1:
            v, n = next(iter(base_set))
            pick = vals[0][1]
            note = "baseline chung"
        elif len(vals) > 1 and len(cur_set) == 1:
            v, n = next(iter(cur_set))
            pick = vals[0][1]
            note = "gia tri chung"
        else:
            v, pick, _t0 = vals[0]
            n = _row_n(pick)
            note = None
            if len(vals) > 1:
                warnings.append(
                    "field '%s' co %d hang khac nhau, lay hang dau" % (key, len(vals)))
        direction = _norm_direction(pick.get("direction")
                                    or (pairs[0][1].get("metric")), "higher")
        out[key] = {"value": v, "n": n, "direction": direction,
                    "ci_eps": _row_ci_eps(pick, v),
                    "is_total": _is_total_key(key),
                    "note": note}
    return out, skipped_test, warnings


def _classify_eval_file(ev):
    """'contract' neu co eval_contract; 'simple' neu 1 bang don gian;
    'ambiguous' neu khong doan duoc (nhieu bang / hang ablation)."""
    if isinstance(ev.get("eval_contract"), dict):
        return "contract"
    tables = ev.get("tables", []) or []
    if len(tables) == 1 and not _looks_like_ablation_table(tables[0]):
        rows = tables[0].get("rows", []) or []
        if all(not _looks_like_variant_row((r or {}).get("item")) for r in rows
               if isinstance(r, dict)):
            return "simple"
    if not tables:
        return "simple"
    return "ambiguous"


def read_field_history(run_dir, policy=None):
    """ Lich su metric theo field tu reports/round-*/eval.json (sap xep theo ten dir).

    Tra (fields, skipped) voi fields[field] = [{round, value, n, direction,
    ci_eps, is_total, eval_file}]. Chi doc val/OOF: ban ghi nao khai split/test
    thi BO QUA. Khong co nguon metric khai bao ma file mo ho -> raise
    OptimizeError (fail-closed, khong doan). Nhieu eval.json cung vong -> ban
    moi nhat. Hang tong 'ALL' giu de bao cao (is_total), khong de sua.
    """
    if policy is None:
        _, policy, _ = load_policy(run_dir)
        policy = policy or {}
    by_round = _eval_files_by_round(run_dir)
    if not by_round:
        return {}, []
    use_declared = policy.get("metrics_source") is not None
    fields = {}
    skipped_test = []
    pending_ambiguous = []
    for rnd in sorted(by_round):
        p = by_round[rnd][-1]
        try:
            ev = _load_eval_file(p)
        except (OSError, ValueError):
            continue
        kind = "declared" if use_declared else _classify_eval_file(ev)
        if kind == "ambiguous":
            pending_ambiguous.append(p.replace(os.sep, "/"))
            continue
        try:
            resolved, skipped, _warn = _resolve_file_metrics(ev, policy, kind)
        except OptimizeError:
            raise
        skipped_test.extend("%s:%s" % (rnd, s) for s in skipped)
        for key, e in resolved.items():
            fields.setdefault(key, []).append({
                "round": rnd, "value": e["value"], "n": e["n"],
                "direction": e["direction"], "ci_eps": e["ci_eps"],
                "is_total": e["is_total"],
                "eval_file": p.replace(os.sep, "/"),
            })
    if pending_ambiguous and not fields and not use_declared:
        raise OptimizeError(
            "khong xac dinh duoc nguon metric: %d eval.json (vd. %s) khong co "
            "eval_contract, co nhieu bang hoac hang ablation (vd. 'RPA +P "
            "+augment ... hieu -3.9d CI95 ...'), ma policy thieu metrics_source. "
                "KHONG tu doan bang. Chay `python scripts/optimize.py init "
                "<run_dir>` de liet ke cac bang ung vien roi chon bang "
                "`init --metrics-table <index|regex> --field-col <cot> "
                "--value-col <cot|auto>`" % (len(pending_ambiguous),
                                             pending_ambiguous[0]))
    return fields, skipped_test


def std_error(value, n):
    """Sai so chuan nhi thuc sqrt(p(1-p)/n); None khi thieu n."""
    if n is None or n <= 0:
        return None
    p = min(max(value, 0.0), 1.0)
    return math.sqrt(p * (1.0 - p) / n)


def effective_epsilon(policy, value, n, ci_eps=None):
    """Epsilon hieu dung: policy.epsilon, hoac cot CI/sai so chuan cua hang
    (neu co), hoac mac dinh theo sai so chuan bo danh gia (1/2 * SE) neu co n;
    cuoi cung 0.005."""
    if policy.get("epsilon") is not None:
        return float(policy["epsilon"])
    if ci_eps is not None and ci_eps > 0:
        return float(ci_eps)
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
    """{component: diagnosis_dict} tu diagnosis/*/diagnosis.json (+ diagnosis.json cu).
    Index ca theo diagnosis.component (khoa chuan = field key trong eval) va theo
    ten thu muc (slug ngan ma worker dung), de task DIAG tim duoc du worker dat
    ten thu muc theo slug."""
    out = {}
    for p in sorted(glob.glob(diagnosis_glob(run_dir))):
        try:
            with open(p, encoding="utf-8-sig") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        comp = d.get("component") or os.path.basename(os.path.dirname(p))
        out[str(comp)] = d
        out.setdefault(os.path.basename(os.path.dirname(p)), d)
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


# --- Target hieu dung (thieu target -> KHONG GO, OP3) ---

def effective_target(policy, field):
    """(target, nguon) voi nguon trong {'policy', 'default', None}.
    targets.<field>.target truoc, roi default_target; ca hai thieu -> None."""
    t = (policy.get("targets", {}) or {}).get(field, {})
    if isinstance(t, dict) and isinstance(t.get("target"), (int, float)) \
            and not isinstance(t.get("target"), bool):
        return float(t["target"]), "policy"
    dft = policy.get("default_target")
    if isinstance(dft, (int, float)) and not isinstance(dft, bool):
        return float(dft), "default"
    return None, None


def _read_text_candidates(run_dir):
    out = []
    for name in ("spec.md", "plan.json"):
        p = os.path.join(os.path.abspath(run_dir), name)
        try:
            with open(p, encoding="utf-8-sig") as f:
                out.append((name, f.read()))
        except OSError:
            pass
    return out


def _fold_ascii(text):
    s = str(text).replace("\u0111", "d").replace("\u0110", "D")
    s = unicodedata.normalize("NFKD", s)
    return s.encode("ascii", "ignore").decode("ascii").lower()


def extract_default_target_from_texts(texts):
    """Trich target 'moi field >= NN%' ro rang tu spec/plan.
    Tra (value, snippet) hoac (None, None) khi khong ro -> khong doan."""
    pats = [
        r"moi\s+(field|truong|cot)\s*[>\u2265]=?\s*(\d+(?:[.,]\d+)?)\s*%",
        r"moi\s+(field|truong|cot)\s*[>\u2265]\s*0[.,](\d+)",
    ]
    for fname, text in texts:
        low = _fold_ascii(text)
        for pat in pats:
            m = re.search(pat, low)
            if m:
                try:
                    num = float(m.group(2).replace(",", "."))
                except ValueError:
                    continue
                val = num / 100.0 if num > 1.0 else num
                if 0 < val <= 1.0:
                    i = max(0, m.start() - 20)
                    return val, "%s: ...%s..." % (
                        fname, low[i:m.end() + 20].replace("\n", " ").strip())
    return None, None


def spec_target_hint(run_dir):
    """Goi y target mac dinh tu spec/plan (neu trich duoc ro rang)."""
    val, snip = extract_default_target_from_texts(_read_text_candidates(run_dir))
    if val is not None:
        return ("goi y tu spec (%s): dat policy.default_target=%.4g hoac "
                "policy.targets.<field>.target" % (snip, val))
    return ("khong thay target ro rang trong spec.md/plan.json; hoi nguoi quyet "
            "dinh target moi field (vd. dua theo prior art / ceiling.json)")


# --- Bang status ---

def build_status(run_dir):
    status, policy, errs = load_policy(run_dir)
    if status != "ok":
        raise OptimizeError("policy %s: %s" % (status, "; ".join(errs)))
    fields, skipped = read_field_history(run_dir, policy)
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
        ci_eps = hist[-1].get("ci_eps")
        target, tsrc = effective_target(policy, field)
        eps = effective_epsilon(policy, latest, n, ci_eps)
        is_total = bool(hist[-1].get("is_total"))
        if target is None:
            gap, verdict = None, "thieu target (can nguoi duyet)"
        else:
            gap = _improvement(float(target), latest, direction)
            if gap <= 0:
                verdict = "dat"
            elif upper is not None and float(target) > upper and direction == "higher":
                verdict = "vuot ceiling: hoi nguoi"
            else:
                verdict = "chua dat"
        if is_total:
            verdict = "%s [tong ALL: chi bao cao, khong sua]" % verdict
        trend = latest - hist[-2]["value"] if len(hist) >= 2 else 0.0
        rows.append({
            "field": field, "baseline": base, "latest": latest, "n": n,
            "target": target, "target_source": tsrc, "gap": gap, "trend": trend,
            "rounds_used": used, "epsilon": eps, "verdict": verdict,
            "direction": direction, "is_total": is_total,
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
    fields, _ = read_field_history(run_dir, policy)
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
    actionable = {f: h for f, h in latest_of.items() if not h.get("is_total")}
    if not actionable:
        return {"decision": STOP_ASK, "stop": True,
                "reason": "chi con hang tong 'ALL' (bao cao, khong sua): "
                          "khai bao metrics_source dung bang metric theo field",
                "round": int(st.get("next_round", 1)), "tasks": []}
    missing_target = sorted(f for f in actionable
                            if effective_target(policy, f)[0] is None)
    if missing_target:
        return {"decision": STOP_ASK, "stop": True,
                "reason": "thieu target cho field: %s. %s" % (
                    ", ".join(missing_target), spec_target_hint(run_dir)),
                "round": int(st.get("next_round", 1)), "tasks": [],
                "missing_targets": missing_target}
    unmet = []
    for f, h in actionable.items():
        t, _src = effective_target(policy, f)
        if t is None:
            raise OptimizeError("internal: field '%s' thieu target "
                                "(phai STOP-hoi-nguoi truoc do)" % f)
        if _improvement(float(t), h["value"], h.get("direction", "higher")) > 0:
            unmet.append(f)
    if not unmet:
        return {"decision": STOP_SUCCESS, "stop": True,
                "reason": "moi target da dat; task cuoi I-final (test khoa mot lan qua seal) roi release",
                "round": int(st.get("next_round", 1)), "tasks": [],
                "final_task": "I-final"}

    for f in unmet:
        t, _src = effective_target(policy, f)
        d = diags.get(f, {})
        v = norm_verdict(d.get("verdict"))
        if v in ("OBJECTIVE", "NOISE"):
            return {"decision": STOP_ASK, "stop": True,
                    "reason": "field '%s' verdict %s can nguoi (doi metric/spec hoac ha target)" % (f, v),
                    "round": int(st.get("next_round", 1)), "tasks": [], "field": f}
        if (t is not None and upper is not None
                and _improvement(float(t), actionable[f]["value"],
                                 actionable[f].get("direction", "higher")) > 0
                and float(t) > upper and actionable[f].get("direction", "higher") == "higher"):
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
            eps_ref = effective_epsilon(policy, actionable[f0]["value"],
                                        actionable[f0].get("n"),
                                        actionable[f0].get("ci_eps"))
        if all(g < eps_ref for g in tail):
            return {"decision": STOP_PLATEAU, "stop": True,
                    "reason": "cai thien %s < epsilon %.4g trong %d vong lien tiep" % (
                        "[" + ", ".join("%.4g" % g for g in tail) + "]", eps_ref, patience),
                    "round": int(st.get("next_round", 1)), "tasks": []}

    scored = []
    for f in unmet:
        t, _src = effective_target(policy, f)
        gap = 1.0 if t is None else max(
            _improvement(float(t), actionable[f]["value"], actionable[f].get("direction", "higher")), 0.0)
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
        eff_t, _src = effective_target(policy, f)
        branch_tasks = tasks_for_verdict(rnd, f, actionable[f], {"target": eff_t},
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


def _slug(text, max_len=24):
    """Slug ascii ngan gon: bo dau/ký tu la, toi da max_len ky tu."""
    s = str(text).replace("\u0111", "d").replace("\u0110", "D")
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"
    s = s[:max_len].rstrip("-") or "x"
    return s


def _task_hash(rnd, field, action):
    """6 ky tu hex on dinh: cung (vong, field, action) -> cung id."""
    raw = "%02d|%s|%s" % (rnd, field, action)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]


def _task_id(rnd, field, action):
    """R<NN>-<slug toi da 24 ky tu>-<hash 6>-<action> (on dinh, idempotent)."""
    return "R%02d-%s-%s-%s" % (rnd, _slug(field), _task_hash(rnd, field, action),
                               action)


def diag_task(rnd, field, agent, split):
    comp = _slug(field)
    tid = _task_id(rnd, field, "diag")
    return {
        "id": tid,
        "title": "R%02d: chan doan '%s'" % (rnd, field),
        "role": "weakness-diagnostician",
        "agent": agent,
        "mode": "evaluate-only",
        "resources": {"compute": "cpu"},
        "worktree": "new-child",
        "deps": [],
        "owns": ["runs/<run_id>/diagnosis/%s/" % comp, "runs/<run_id>/artifacts/%s/" % tid],
        "inputs": ["reports/round-*/eval.json (val/OOF)", "runs/<id>/playbook.json"],
        "outputs": ["runs/<id>/diagnosis/%s/diagnosis.json" % comp],
        "target": "xac minh NGUYEN NHAN '%s' (DATA/MODEL/STRUCTURE/OBJECTIVE/NOISE) bang thi nghiem phan biet re nhat" % field,
        "change": ("Chay thi nghiem phan biet tren split %s (KHONG dung test): Bobby hoc, "
                   "slice, oracle, overfit tap con, doc mu. Ghi diagnosis.json "
                   "voi \"component\" dung la '%s' (verdict + shares + actions co predicted_gain)." % (split, field)),
        "acceptance": "diagnosis.json co component '%s' + verdict thuoc DATA/MODEL/STRUCTURE/OBJECTIVE/NOISE + evidence moi test; predicted_gain=0.0 (chan doan, khong sua)" % field,
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
                tid = _task_id(rnd, field, "research")
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
            tid = _task_id(rnd, field, "collect")
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
        tid = _task_id(rnd, field, "retrain")
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
        tid = _task_id(rnd, field, "retrain")
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
                tid = _task_id(rnd, field, "research")
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
        tid = _task_id(rnd, field, "aux")
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
        tid2 = _task_id(rnd, field, "integrate")
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
        tid = _task_id(rnd, field, "postprocess")
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


def _eval_values(ev, policy=None):
    """{field: {value, n, direction}} cua MOT eval.json, dung chung bo giai
    metric voi status/next (metrics_source / eval_contract / file don gian).
    File mo ho ma khong co khai bao -> {} (record bao loi rieng)."""
    policy = policy or {}
    try:
        kind = "declared" if policy.get("metrics_source") is not None \
            else _classify_eval_file(ev)
        if kind == "ambiguous":
            return {}
        resolved, _skipped, _warn = _resolve_file_metrics(ev, policy, kind)
    except (OptimizeError, ValueError):
        return {}
    return {f: {"value": e["value"], "n": e["n"],
                "direction": e["direction"], "ci_eps": e.get("ci_eps")}
            for f, e in resolved.items()}


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
    new_vals = _eval_values(new_ev, policy)
    if not new_vals:
        raise OptimizeError("eval.json moi (%s) khong trich duoc metric nao "
                            "(file mo ho ma thieu metrics_source, hoac rong)" % newest)
    if len(dirs) >= 2 and os.path.isfile(os.path.join(dirs[-2], "eval.json")):
        try:
            old_vals = _eval_values(_read_eval_file(os.path.join(dirs[-2], "eval.json")),
                                    policy)
        except ValueError:
            old_vals = {}
    else:
        fields, _ = read_field_history(run_dir, policy)
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
        eps_ref = effective_epsilon(policy, new_vals[f0]["value"],
                                    new_vals[f0].get("n"),
                                    new_vals[f0].get("ci_eps"))
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

def _candidate_tables(run_dir):
    """Quet eval.json moi nhat: [(index, name, n_rows, sample_items, ablation_like)]."""
    by_round = _eval_files_by_round(run_dir)
    if not by_round:
        return None, []
    rnd = sorted(by_round)[-1]
    p = by_round[rnd][-1]
    try:
        ev = _load_eval_file(p)
    except (OSError, ValueError) as e:
        raise OptimizeError("khong doc duoc eval.json moi nhat %s (%s)" % (p, e))
    cands = []
    for i, t in enumerate(ev.get("tables", []) or []):
        if not isinstance(t, dict):
            continue
        rows = t.get("rows", []) or []
        sample = [str((r or {}).get("item") or "?")[:70] for r in rows[:3]]
        cands.append({"index": i, "name": str(t.get("name") or "?"),
                      "n_rows": len(rows), "sample": sample,
                      "ablation_like": _looks_like_ablation_table(t)})
    return p, cands


def print_candidates(run_dir):
    try:
        p, cands = _candidate_tables(run_dir)
    except OptimizeError as e:
        print("  (khong liet ke duoc bang: %s)" % e)
        return
    if p is None:
        print("  (chua co reports/round-*/eval.json de quet bang)")
        return
    print("  Bang ung vien trong %s (moi nhat):" % p.replace(os.sep, "/"))
    for c in cands:
        flag = " [GIONG ABLATION/SO SANH BIEN THE - thuong KHONG phai bang metric]" \
            if c["ablation_like"] else ""
        print("    [%d] '%s' (%d hang)%s" % (c["index"], c["name"], c["n_rows"], flag))
        for s in c["sample"]:
            print("        vd: %s" % s)
    print("  Chon bang metric theo field (KHONG chon bang ablation), roi chay:")
    print("    python scripts/optimize.py init <run_dir> --metrics-table <index|regex>"
          " --field-col <cot ten> --value-col <cot gia tri|auto>")


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
    if pol.get("default_target") is None:
        val, snip = extract_default_target_from_texts(
            _read_text_candidates(a.run_dir))
        if val is not None:
            pol["default_target"] = val
            pol["default_target_source"] = snip
            print("  tu dien default_target=%.4g tu %s" % (val, snip))
        else:
            print("  (khong trich duoc target ro rang tu spec.md/plan.json: "
                  "de default_target=null, can nguoi duyet)")
    p = policy_path(a.run_dir)
    existed = os.path.exists(p)
    if not existed:
        os.makedirs(os.path.dirname(p), exist_ok=True)

        def fn(_old):
            return pol
        statefile.update_json(p, fn, default={})
        os.makedirs(optimize_dir(a.run_dir), exist_ok=True)
        print("da tao %s (nguoi duyet o G2 cung playbook)" % p)
    else:
        print("da co %s (khong ghi de; trinh nguoi duyet o G2 cung playbook)" % p)
    if a.metrics_table is not None:
        try:
            ms = _build_metrics_source(a.run_dir, a)
        except OptimizeError as e:
            print("LOI: %s" % e, file=sys.stderr)
            return 2

        def fn2(_old):
            d = dict(_old) if isinstance(_old, dict) else {}
            d["metrics_source"] = ms
            return d
        statefile.update_json(p, fn2, default={})
        print("da ghi metrics_source=%s vao %s"
              % (json.dumps(ms, ensure_ascii=False), p))
    print_candidates(a.run_dir)
    return 0


def _build_metrics_source(run_dir, a):
    """Dung metrics_source tu --metrics-table <index|regex> + cot."""
    spec = (a.metrics_table or "").strip()
    try:
        p, cands = _candidate_tables(run_dir)
    except OptimizeError as e:
        raise OptimizeError("khong quet duoc bang: %s" % e)
    if p is None or not cands:
        raise OptimizeError("chua co reports/round-*/eval.json de chon bang metric")
    ms = {}
    try:
        idx = int(spec)
        names = [c["name"] for c in cands]
        if idx < 0 or idx >= len(cands):
            raise OptimizeError("table index %d ngoai pham vi (co %d bang: %s)"
                                % (idx, len(cands), names))
        if cands[idx]["ablation_like"]:
            print("  CANH BAO: bang [%d] '%s' trong giong ablation/so sanh bien "
                  "the; chac chan day la bang metric theo field chu?"
                  % (idx, cands[idx]["name"]))
        ms["table_index"] = idx
    except ValueError:
        try:
            rx = re.compile(spec)
        except re.error as e:
            raise OptimizeError("metrics-table regex khong hop le (%s)" % e)
        hit = [c for c in cands if rx.search(c["name"])]
        if not hit:
            raise OptimizeError("regex %r khong khop bang nao (%s)"
                                % (spec, [c["name"] for c in cands]))
        ms["table_title_regex"] = spec
    ms["field_column"] = a.field_col or "item"
    ms["value_column"] = a.value_col or "auto"
    if getattr(a, "field_regex", None):
        ms["field_regex"] = a.field_regex
    if getattr(a, "exclude_regex", None):
        ms["exclude_regex"] = a.exclude_regex
    errs = validate_metrics_source(ms)
    if errs:
        raise OptimizeError("; ".join(errs))
    return ms


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
    p_init.add_argument("--metrics-table", default=None,
                        help="Chon bang metric: index (vd. 0) hoac regex ten bang "
                             "(vd. 'EM tung field'); regex khop nhieu bang thi dung "
                             "tat ca bang khop")
    p_init.add_argument("--field-col", default=None,
                        help="Ten cot ten field (mac dinh item)")
    p_init.add_argument("--value-col", default=None,
                        help="Ten cot gia tri (mac dinh auto: value hoac correct/total)")
    p_init.add_argument("--field-regex", default=None,
                        help="Regex trich khoa field tu ten hang (nhom 1 neu co)")
    p_init.add_argument("--exclude-regex", default=None,
                        help="Regex loai hang (vd. bien the ablation)")
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
