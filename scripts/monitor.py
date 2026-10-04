#!/usr/bin/env python3
"""Giam sat sau release theo control band (1sigma/2sigma/3sigma) + drift PSI/KS + incident -> INTENT.

Vong doi: model da release -> do tin hieu van hanh (co nhan va khong nhan) -> so voi
baseline da duyet -> vuot band thi mo INCIDENT (intent) de tai nhap pipeline, KHONG tu
rollback va KHONG tu sua.

Cac lop:
  (1) DU LIEU  runs/<id>/monitor/metrics.jsonl (append-only, qua statefile.append_jsonl)
      moi dong {ts, metric, value, labels{model_version, slice}, n}.
  (2) POLICY   runs/<id>/monitor_policy.json (version, nguoi duyet o G2; xem
      schemas/monitor_policy.schema.json + templates/monitor_policy.template.json).
  (3) CHECK    monitor.py check <run_dir> [--now ISO] [--json]
      tinh band dung huong, xu ly sigma=0 / baseline qua ngan / min_n, ghi
      monitor/state.json + monitor/actions.jsonl (append-only, idempotent), mo
      monitor/incidents/<id>.md cho tang >= diagnose, ghi Incident vao KG (kg.py) va
      ghi so thi nghiem type=error. KHONG tu rollback, khong goi Orca/mang.

Commands:
  monitor.py record <run_dir> --metric M --value V [--n N] [--label k=v ...] [--now ISO]
  monitor.py ingest <run_dir> <file.jsonl|file.csv>
  monitor.py drift [--ref ref.json] [--cur cur.json] [--bins N] [--type numeric|categorical] [--json]
  monitor.py check <run_dir> [--now ISO] [--policy policy.json] [--json]
  monitor.py list <run_dir> [--json]
  monitor.py dismiss <run_dir> <incident_id> --reason "..." [--actor who]
  monitor.py resolve <run_dir> <incident_id> [--reason "..."] [--actor who]

Ma thoat (nhat quan, supervisor/coordinator dua vao):
  0 = on (moi metric trong band)
  1 = co canh bao hoac vuot band nhe (warn; no_data cung tinh la canh bao)
  2 = vuot band tang diagnose/propose hoac incident/stale dang mo (can nguoi)
  3 = loi du lieu/policy (fail-closed: policy thieu/sai enum/sai thu tu band, metrics hong...)

Stdlib only. Da nen tang (pathlib/os.path, utf-8, newline='\\n' khi ghi file sinh ra).
"""

import argparse
import contextlib
import csv
import datetime as dt
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
import statefile  # noqa: E402

try:
    import kg  # noqa: E402  (single validated write API for the knowledge graph)
except ImportError:  # pragma: no cover - scripts/ luon di kem
    kg = None

DIRECTIONS = ("higher_is_better", "lower_is_better")
DEFAULT_BANDS = {"warn": 1.0, "diagnose": 2.0, "propose": 3.0}
TIER_RANK = {"ok": 0, "no_data": 1, "warn": 1, "diagnose": 2, "stale": 2, "propose": 3}
INCIDENT_TIERS = ("diagnose", "propose", "stale")
ACTION_OF_TIER = {
    "ok": "log",
    "no_data": "chờ dữ liệu",
    "warn": "log + theo dõi",
    "diagnose": "diagnose (chỉ đọc: tạo bản phân tích gợi ý; chưa hành động)",
    "propose": "propose (mở đề xuất rollback hoặc vòng sửa; CẦN NGƯỜI duyệt)",
    "stale": "stale (kiểm tra nguồn/đường ống dữ liệu - dữ liệu ngừng chảy)",
}


class MonitorError(RuntimeError):
    """Loi cau hinh/du lieu: fail-closed (khong doan, khong ghi de)."""


# --- Duong dan ---

def monitor_dir(run_dir):
    return os.path.join(os.path.abspath(run_dir), "monitor")


def metrics_path(run_dir):
    return os.path.join(monitor_dir(run_dir), "metrics.jsonl")


def incidents_dir(run_dir):
    return os.path.join(monitor_dir(run_dir), "incidents")


def incidents_registry(run_dir):
    return os.path.join(monitor_dir(run_dir), "incidents.jsonl")


def incident_events_path(run_dir):
    return os.path.join(monitor_dir(run_dir), "incident_events.jsonl")


def actions_path(run_dir):
    return os.path.join(monitor_dir(run_dir), "actions.jsonl")


def state_path(run_dir):
    return os.path.join(monitor_dir(run_dir), "state.json")


def policy_path(run_dir, override=None):
    return override or os.path.join(os.path.abspath(run_dir), "monitor_policy.json")


def _check_guard(run_dir):
    return os.path.join(monitor_dir(run_dir), ".check")


# --- Thoi gian ---

def utcnow():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    """Doc ISO/space timestamp -> datetime co timezone UTC; None neu khong doc duoc."""
    if not s:
        return None
    s = str(s).strip().replace("T", " ").rstrip("Z").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)", s)
    if not m:
        return None
    s = m.group(1)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return None


# --- Thong ke thuan stdlib ---

def mean(vals):
    return sum(vals) / len(vals) if vals else None


def stdev(vals):
    """Do lech chuan mau (n-1). <2 gia tri -> 0.0 (khong chia 0)."""
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))


def deviation(value, center, direction):
    """>0 nghia la XAU hon center dung theo huong metric."""
    return (center - value) if direction == "higher_is_better" else (value - center)


# --- PSI / KS ---

def _psi_from_counts(expected, actual, eps=1e-6):
    ne, na = sum(expected), sum(actual)
    k = len(expected)
    if ne == 0 or na == 0 or k == 0:
        return 0.0
    psi = 0.0
    for i in range(k):
        pe = (expected[i] + eps) / (ne + eps * k)
        pa = (actual[i] + eps) / (na + eps * k)
        psi += (pa - pe) * math.log(pa / pe)
    return psi


def psi_numeric(ref, cur, bins=10):
    """PSI cho dac trung so: chia bins deu tren [min(ref), max(ref)], gom hai bien."""
    if not ref or not cur:
        return 0.0
    lo, hi = min(ref), max(ref)
    if hi == lo:
        return 0.0  # mot bin duy nhat: phan bo giong nhau
    width = (hi - lo) / bins

    def assign(v):
        if v <= lo:
            return 0
        if v >= hi:
            return bins - 1
        return min(bins - 1, int((v - lo) / width))

    ref_c = [0] * bins
    cur_c = [0] * bins
    for v in ref:
        ref_c[assign(v)] += 1
    for v in cur:
        cur_c[assign(v)] += 1
    return _psi_from_counts(ref_c, cur_c)


def psi_categorical(ref, cur):
    """PSI cho dac trung phan loai: tinh tren tap phan loai cua ca ref lan cur."""
    if not ref or not cur:
        return 0.0
    keys = sorted(set(ref) | set(cur), key=str)
    ref_c = [sum(1 for v in ref if v == k) for k in keys]
    cur_c = [sum(1 for v in cur if v == k) for k in keys]
    return _psi_from_counts(ref_c, cur_c)


def ks_two_sample(ref, cur):
    """Kolmogorov-Smirnov hai mau (so): tra (D, p-value asymptotic)."""
    n1, n2 = len(ref), len(cur)
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    sr, sc = sorted(ref), sorted(cur)

    def cdf(sorted_vals, x):
        lo, hi = 0, len(sorted_vals)
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_vals[mid] <= x:
                lo = mid + 1
            else:
                hi = mid
        return lo / len(sorted_vals)

    d = 0.0
    for x in sorted(set(ref) | set(cur)):
        d = max(d, abs(cdf(sr, x) - cdf(sc, x)))
    return d, ks_pvalue(d, n1, n2)


def ks_pvalue(d, n1, n2):
    if d <= 0 or n1 == 0 or n2 == 0:
        return 1.0
    ne = n1 * n2 / (n1 + n2)
    lam = (math.sqrt(ne) + 0.12 + 0.11 / math.sqrt(ne)) * d
    total = 0.0
    for k in range(1, 100):
        term = 2 * ((-1) ** (k - 1)) * math.exp(-2 * k * k * lam * lam)
        total += term
        if abs(term) < 1e-10:
            break
    return max(0.0, min(1.0, total))


# --- Policy ---

def load_policy(run_dir, override=None):
    """Tra (status, policy, errors). status: ok|missing|corrupt|invalid (fail-closed)."""
    p = policy_path(run_dir, override)
    try:
        with open(p, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return "missing", None, [f"chưa có monitor_policy.json ({p}); copy từ templates/monitor_policy.template.json và duyệt ở G2"]
    except OSError as e:
        return "corrupt", None, [f"không đọc được policy {p}: {e}"]
    if not text.strip():
        return "corrupt", None, [f"{p} tồn tại nhưng rỗng; sửa/xóa tay hoặc khôi phục bản sao, KHÔNG tự ghi đè"]
    try:
        pol = json.loads(text)
    except ValueError as e:
        return "corrupt", None, [f"monitor_policy.json không phải JSON hợp lệ ({e})"]
    errs = validate_policy(pol)
    if errs:
        return "invalid", pol, errs
    return "ok", pol, []


def validate_policy(pol):
    """List loi (rong = hop le). Khong exit; CLI tu exit."""
    if not isinstance(pol, dict):
        return ["policy phải là object JSON"]
    errs = []
    for k in ("run_id", "policy_version", "metrics"):
        if k not in pol:
            errs.append(f"thiếu trường bắt buộc '{k}'")
    if "policy_version" in pol and not str(pol.get("policy_version") or "").strip():
        errs.append("policy_version phải là chuỗi không rỗng")
    stale = pol.get("stale_after_minutes")
    if stale is not None and (not isinstance(stale, int) or isinstance(stale, bool) or stale <= 0):
        errs.append("stale_after_minutes phải là số nguyên > 0 (hoặc bỏ trống)")
    metrics = pol.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        errs.append("metrics phải là object không rỗng")
        return errs
    for name, entry in metrics.items():
        prefix = f"metrics.{name}"
        if not isinstance(entry, dict):
            errs.append(f"{prefix} phải là object")
            continue
        if entry.get("direction") not in DIRECTIONS:
            errs.append(f"{prefix}.direction phải thuộc {DIRECTIONS}")
        base = entry.get("baseline")
        if not isinstance(base, dict) or not any(k in base for k in ("value", "window", "values")):
            errs.append(f"{prefix}.baseline phải có một trong: value | window | values")
        else:
            if "window" in base and (not isinstance(base["window"], int) or isinstance(base["window"], bool) or base["window"] < 1):
                errs.append(f"{prefix}.baseline.window phải là số nguyên >= 1")
            if "values" in base and not isinstance(base["values"], list):
                errs.append(f"{prefix}.baseline.values phải là list")
            if "value" in base and not isinstance(base["value"], (int, float)):
                errs.append(f"{prefix}.baseline.value phải là số")
        mn = entry.get("min_n")
        if mn is not None and (not isinstance(mn, int) or isinstance(mn, bool) or mn < 1):
            errs.append(f"{prefix}.min_n phải là số nguyên >= 1")
        has_sigma = "bands" in entry
        has_abs = "bands_abs" in entry
        if not has_sigma and not has_abs:
            errs.append(f"{prefix} phải có 'bands' (σ) hoặc 'bands_abs' (ngưỡng tuyệt đối)")
        for bkey in ("bands", "bands_abs"):
            bands = entry.get(bkey)
            if bands is None:
                continue
            if not isinstance(bands, dict):
                errs.append(f"{prefix}.{bkey} phải là object")
                continue
            for thr in ("warn", "diagnose", "propose"):
                if thr not in bands:
                    errs.append(f"{prefix}.{bkey} thiếu ngưỡng '{thr}'")
                elif not isinstance(bands[thr], (int, float)) or isinstance(bands[thr], bool):
                    errs.append(f"{prefix}.{bkey}.{thr} phải là số")
            ordered = [bands.get(t) for t in ("warn", "diagnose", "propose")]
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in ordered):
                if min(ordered) < 0:
                    errs.append(f"{prefix}.{bkey} ngưỡng phải >= 0")
                elif not (ordered[0] <= ordered[1] <= ordered[2]):
                    errs.append(f"{prefix}.{bkey} phải tăng dần: warn <= diagnose <= propose")
        if has_sigma and not has_abs:
            base = base if isinstance(base, dict) else {}
            if not ("window" in base or "values" in base):
                errs.append(f"{prefix}.bands (σ) cần baseline.window hoặc baseline.values để tính độ lệch chuẩn")
    return errs


def baseline_values(entry, history):
    """Danh sach gia tri baseline tu baseline.window/values (history = ban ghi truoc do)."""
    base = entry.get("baseline") or {}
    if "values" in base:
        return [float(v) for v in base["values"]]
    if "window" in base:
        n = int(base["window"])
        return [float(r["value"]) for r in history[-n:]]
    return []


def evaluate_entry(entry, value, n, history):
    """Tra dict ket qua band cho mot metric (khong ghi file)."""
    direction = entry["direction"]
    base = entry.get("baseline") or {}
    if "value" in base and "window" not in base and "values" not in base:
        center, sigma = float(base["value"]), None
    else:
        vals = baseline_values(entry, history)
        if not vals:
            return {
                "tier": "no_data", "band": "-", "value": value, "n": n,
                "center": None, "sigma": None, "measure": None, "measure_kind": None,
                "reason": "chưa đủ dữ liệu baseline (cần thêm mẫu tham chiếu)",
            }
        center, sigma = mean(vals), stdev(vals)

    dev = deviation(value, center, direction)
    if "bands_abs" in entry:
        bands = entry["bands_abs"]
        measure, kind = dev, "abs"
    else:
        bands = entry.get("bands") or DEFAULT_BANDS
        kind = "z"
        if sigma is None or sigma == 0:
            if dev <= 0:
                return {
                    "tier": "ok", "band": "σ=0", "value": value, "n": n,
                    "center": center, "sigma": sigma, "measure": 0.0, "measure_kind": "z",
                    "reason": "baseline σ=0; giá trị không xấu hơn baseline",
                }
            return {
                "tier": "propose", "band": "σ=0", "value": value, "n": n,
                "center": center, "sigma": sigma, "measure": None, "measure_kind": "z",
                "reason": "baseline σ=0 nhưng giá trị lệch theo hướng xấu: cần người xem",
            }
        measure = dev / sigma
    tier = _band_tier(measure, bands)
    band = f"{measure:.2f}" + ("σ" if kind == "z" else "")
    reason = f"{'lệch' if dev > 0 else 'tốt hơn'} baseline {abs(dev):.4g} (σ={sigma if sigma is None else round(sigma, 6)})"
    mn = entry.get("min_n")
    if mn and n is not None and n < mn and tier in ("diagnose", "propose"):
        tier = "warn"
        reason += f"; n={n} < min_n={mn} nên hạ tầng về warn"
    return {
        "tier": tier, "band": band, "value": value, "n": n, "center": center,
        "sigma": sigma, "measure": measure, "measure_kind": kind, "reason": reason,
    }


def _band_tier(measure, bands):
    warn = float(bands.get("warn", DEFAULT_BANDS["warn"]))
    diag = float(bands.get("diagnose", DEFAULT_BANDS["diagnose"]))
    prop = float(bands.get("propose", DEFAULT_BANDS["propose"]))
    if measure is None:
        return "ok"
    if measure >= prop:
        return "propose"
    if measure >= diag:
        return "diagnose"
    if measure >= warn:
        return "warn"
    return "ok"


def is_stale(latest_ts, now, stale_after_minutes):
    if not stale_after_minutes or not latest_ts or now is None:
        return False
    t = parse_ts(latest_ts)
    if t is None:
        return False
    return (now - t).total_seconds() > stale_after_minutes * 60


# --- Doc metrics ---

def read_metrics(run_dir):
    """Doc metrics.jsonl strict: dong hong o GIUA file -> MonitorError ro rang."""
    p = metrics_path(run_dir)
    if not os.path.exists(p):
        return []
    try:
        rows = statefile.read_jsonl(p, strict=True)
    except statefile.StateCorrupt as e:
        raise MonitorError(f"metrics.jsonl hỏng: {e}") from e
    for i, rec in enumerate(rows):
        if not isinstance(rec, dict) or not rec.get("metric") or "value" not in rec:
            raise MonitorError(f"metrics.jsonl: bản ghi #{i + 1} thiếu 'metric' hoặc 'value'")
    return rows


# --- Check ---

def run_check(run_dir, now_iso=None, policy_override=None):
    status, policy, errs = load_policy(run_dir, policy_override)
    if status != "ok":
        raise MonitorError(f"policy {status}: " + "; ".join(errs))

    rows = read_metrics(run_dir)
    by_metric = {}
    for rec in rows:
        by_metric.setdefault(rec["metric"], []).append(rec)

    now = parse_ts(now_iso) if now_iso else dt.datetime.now(dt.timezone.utc)
    stale_min = policy.get("stale_after_minutes")
    results = []
    for name, entry in policy["metrics"].items():
        recs = by_metric.get(name, [])
        if not recs:
            results.append({
                "metric": name, "direction": entry.get("direction", "-"), "value": None, "n": None,
                "ts": None, "labels": {}, "tier": "no_data", "band": "-",
                "action": ACTION_OF_TIER["no_data"], "reason": "chưa có bản ghi metrics cho metric này",
                "center": None, "sigma": None, "measure": None,
            })
            continue
        latest = recs[-1]
        n = _as_int(latest.get("n"))
        ev = evaluate_entry(entry, float(latest["value"]), n, recs[:-1])
        if is_stale(latest.get("ts"), now, stale_min):
            ev["tier"] = "stale"
            ev["band"] = "-"
            ev["reason"] = f"dữ liệu ngừng chảy: bản ghi cuối {latest.get('ts')} quá {stale_min} phút"
        results.append({
            "metric": name, "direction": entry.get("direction"), "value": float(latest["value"]),
            "n": n, "ts": latest.get("ts"), "labels": latest.get("labels") or {},
            "tier": ev["tier"], "band": ev["band"], "action": ACTION_OF_TIER[ev["tier"]],
            "reason": ev["reason"], "center": ev.get("center"), "sigma": ev.get("sigma"),
            "measure": ev.get("measure"),
        })
    created = _write_actions_and_incidents(run_dir, policy, results, now)
    _write_state(run_dir, policy, results, now)

    exit_level = 0
    for r in results:
        rank = TIER_RANK.get(r["tier"], 0)
        if rank >= 2:
            exit_level = 2
        elif rank >= 1 and exit_level < 1:
            exit_level = 1
    summary = {t: sum(1 for r in results if r["tier"] == t) for t in ("ok", "no_data", "warn", "diagnose", "propose", "stale")}
    max_tier = max((r["tier"] for r in results), key=lambda t: TIER_RANK.get(t, 0), default="ok")
    report = {
        "run_dir": os.path.abspath(run_dir),
        "policy_version": policy.get("policy_version"),
        "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": results,
        "summary": {**summary, "max_tier": max_tier, "incidents_created": created},
        "exit_level": exit_level,
    }
    return report


def _as_int(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _write_actions_and_incidents(run_dir, policy, results, now):
    created = []
    version = policy.get("policy_version")
    with statefile.file_lock(_check_guard(run_dir)):
        existing_actions = statefile.read_jsonl(actions_path(run_dir))
        seen = {(a.get("metric"), a.get("tier"), a.get("window")) for a in existing_actions}
        registry = statefile.read_jsonl(incidents_registry(run_dir))
        seen_incidents = {(r.get("metric"), r.get("tier"), r.get("window")) for r in registry}
        for r in results:
            tier = r["tier"]
            if tier not in ("warn", "diagnose", "propose", "stale"):
                continue
            window = r.get("ts")
            sig = (r["metric"], tier, window)
            if sig not in seen:
                statefile.append_jsonl(actions_path(run_dir), {
                    "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "metric": r["metric"], "tier": tier, "value": r["value"],
                    "band": r["band"], "policy_version": version, "action": r["action"],
                    "window": window, "n": r.get("n"), "labels": r.get("labels") or {},
                })
                seen.add(sig)
            if tier in INCIDENT_TIERS and sig not in seen_incidents:
                inc = _create_incident(run_dir, policy, r, now)
                seen_incidents.add(sig)
                created.append(inc)
    return created


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "x"


def _compact(ts):
    return re.sub(r"\D", "", str(ts or ""))[:14] or "unknown"


def incident_id(metric, tier, window):
    return f"incident:monitor-{_slug(metric)}-{_compact(window)}-{tier}"


def _create_incident(run_dir, policy, r, now):
    inc_id = incident_id(r["metric"], r["tier"], r.get("ts"))
    filename = f"{_slug(r['metric'])}-{_compact(r.get('ts'))}-{r['tier']}.md"
    body = _incident_markdown(run_dir, policy, r, now)
    os.makedirs(incidents_dir(run_dir), exist_ok=True)
    path = os.path.join(incidents_dir(run_dir), filename)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    statefile.append_jsonl(incidents_registry(run_dir), {
        "id": inc_id, "metric": r["metric"], "tier": r["tier"], "value": r["value"],
        "band": r["band"], "window": r.get("ts"), "path": path, "status": "open",
        "policy_version": policy.get("policy_version"), "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "labels": r.get("labels") or {},
    })
    # Side-effect của notebook/kg (đọc/ghi sổ, KG) có thể in ra stdout. Đẩy sang
    # stderr để stdout của `check --json` chỉ còn JSON hợp lệ (hợp đồng ổn định).
    with contextlib.redirect_stdout(sys.stderr):
        _sync_incident_kg(run_dir, inc_id, r, now)
        _log_notebook_error(run_dir, inc_id, r, now)
    print(f"  [INCIDENT] {r['metric']} [{r['tier']}] -> {path}", file=sys.stderr)
    return inc_id


def _incident_markdown(run_dir, policy, r, now):
    metrics_ref = _metrics_ref(run_dir)
    labels = r.get("labels") or {}
    return "\n".join([
        f"# Sự cố giám sát: {r['metric']} ({r['tier']})",
        "",
        f"- **Thời điểm phát hiện**: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- **Metric**: `{r['metric']}` (direction={r['direction']}, policy={policy.get('policy_version')})",
        f"- **Giá trị hiện tại**: {r['value']} (n={r['n']}); baseline center={r['center']}, σ={r['sigma']}, band={r['band']}",
        f"- **Ngữ cảnh (labels)**: {json.dumps(labels, ensure_ascii=False)}",
        f"- **Bằng chứng**: {metrics_ref} (cửa sổ bản ghi cuối: {r.get('ts')})",
        f"- **Phạm vi ảnh hưởng**: model_version={labels.get('model_version', '-')}, slice={labels.get('slice', '-')}",
        f"- **Lý do phát hiện**: {r['reason']}",
        "",
        "## Đề xuất (INTENT tái nhập pipeline)",
        f"- Tầng `{r['tier']}`: {r['action']}",
        "- Đề xuất: (a) rollback về model_version trước nếu chất lượng giảm, hoặc (b) mở một vòng sửa có giả thuyết rõ ràng (xem skill ai-pipeline-diagnose).",
        "- KHÔNG tự rollback, KHÔNG tự sửa: mọi hành động cần người duyệt.",
        "",
        "- **Người cần duyệt**: person:user (G2)",
        "- **Trạng thái**: open",
        "",
    ])


def _metrics_ref(run_dir):
    p = metrics_path(run_dir)
    if kg is not None:
        rel = kg.normalize_ref_path(p)
        if rel:
            return rel
    return p.replace(os.sep, "/")


def _sync_incident_kg(run_dir, inc_id, r, now):
    if kg is None:
        return
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    title = f"Sự cố giám sát {r['metric']} [{r['tier']}]"
    try:
        kg.upsert_entity(run_dir, inc_id, "Incident", title, body=r["reason"],
                         properties={"metric": r["metric"], "tier": r["tier"], "value": r["value"],
                                     "band": r["band"], "labels": r.get("labels") or {}},
                         created_at=ts)
        art_id = "artifact:monitor-metrics"
        kg.upsert_entity(run_dir, art_id, "Artifact", "Monitor metrics (append-only)",
                         body="Dữ liệu giám sát sau release",
                         properties={"path": _metrics_ref(run_dir), "kind": "monitor_metrics"},
                         created_at=ts)
        kg.add_edge_checked(run_dir, inc_id, art_id, "evidenced_by", valid_from=ts, recorded_at=ts,
                            source_ref=_metrics_ref(run_dir))
    except Exception as e:  # noqa: BLE001 - KG loi khong duoc lam hong check; ghi canh bao
        print(f"  [CẢNH BÁO] không ghi được Incident vào KG ({e}); chạy kg.py sau khi sửa", file=sys.stderr)


def _log_notebook_error(run_dir, inc_id, r, now):
    try:
        import notebook  # noqa: E402
        ns = argparse.Namespace(
            run_dir=run_dir, type="error", title=f"Sự cố giám sát: {r['metric']} ({r['tier']})",
            body=(f"{r['reason']}. value={r['value']} n={r['n']} band={r['band']}. "
                  f"Incident: {inc_id}. Xem monitor/incidents/."),
            tags="monitor,incident", metrics=json.dumps({"value": r["value"]}),
            refs=_metrics_ref(run_dir), author="monitor", no_kg=True, kg_edges=None)
        notebook.cmd_log(ns)
    except Exception as e:  # noqa: BLE001
        print(f"  [CẢNH BÁO] không ghi được sổ thí nghiệm ({e})", file=sys.stderr)


def _write_state(run_dir, policy, results, now):
    def fn(_old):
        return {
            "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "policy_version": policy.get("policy_version"),
            "metrics": {r["metric"]: {"tier": r["tier"], "value": r["value"], "n": r["n"],
                                      "band": r["band"], "ts": r.get("ts"), "action": r["action"]}
                        for r in results},
        }
    statefile.update_json(state_path(run_dir), fn, default={})


def _print_check_table(report):
    print(f"=== Giám sát sau release: {report['run_dir']} | policy {report['policy_version']} | {report['now']} ===")
    print(f"{'metric':28} {'value':>10} {'n':>6} {'band':>10} {'tier':<9} action")
    for r in report["metrics"]:
        v = "-" if r["value"] is None else f"{r['value']:.4g}"
        print(f"{r['metric'][:28]:28} {v:>10} {str(r['n'] if r['n'] is not None else '-'):>6} "
              f"{str(r['band']):>10} {r['tier']:<9} {r['action']}")
        if r["tier"] != "ok":
            print(f"    - {r['reason']}")
    s = report["summary"]
    print(f"Tổng: ok={s['ok']} warn={s['warn']} diagnose={s['diagnose']} propose={s['propose']} "
          f"stale={s['stale']} no_data={s['no_data']} | max_tier={s['max_tier']}")
    if s["incidents_created"]:
        print("Incident mới: " + ", ".join(s["incidents_created"]))
        print("-> xem monitor/incidents/*.md; quyết định (dismiss|resolve) cần người, KHÔNG tự rollback.")
    if report["exit_level"] == 0:
        print("Kết quả: ổn (trong band).")
    elif report["exit_level"] == 1:
        print("Kết quả: CẢNH BÁO (theo dõi thêm).")
    else:
        print("Kết quả: CẦN NGƯỜI (>= diagnose): mở intent/vòng sửa, không tự hành động.")


# --- Commands ---

def cmd_record(a):
    labels = _parse_labels(a.label)
    res = write_record(a.run_dir, a.metric, a.value, a.n, labels, a.now)
    print(f"recorded [{res['metric']}]={res['value']} (n={res['n']}, labels={json.dumps(res['labels'], ensure_ascii=False)})")
    return 0


def _parse_labels(items):
    labels = {}
    for item in items or []:
        if "=" not in item:
            raise MonitorError(f"--label phải có dạng k=v, nhận được: {item!r}")
        k, v = item.split("=", 1)
        labels[k.strip()] = v.strip()
    return labels


def write_record(run_dir, metric, value, n, labels, now_iso=None):
    if not metric or not str(metric).strip():
        raise MonitorError("--metric không được rỗng")
    try:
        val = float(value)
    except (TypeError, ValueError):
        raise MonitorError(f"--value phải là số, nhận được: {value!r}") from None
    if math.isnan(val) or math.isinf(val):
        raise MonitorError("--value phải là số hữu hạn")
    n_int = _as_int(n)
    if n is not None and n_int is None:
        raise MonitorError(f"--n phải là số nguyên, nhận được: {n!r}")
    res = {"ts": now_iso or utcnow(), "metric": str(metric).strip(), "value": val,
           "labels": labels or {}, "n": n_int}
    statefile.append_jsonl(metrics_path(run_dir), res)
    return res


def cmd_ingest(a):
    rows = _read_ingest(a.file)
    if not rows:
        raise MonitorError(f"không có bản ghi hợp lệ trong {a.file}")
    for row in rows:
        write_record(a.run_dir, row["metric"], row["value"], row.get("n"), row.get("labels") or {}, row.get("ts"))
    print(f"ingested {len(rows)} bản ghi từ {a.file}")
    return 0


def _read_ingest(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return _read_csv(path)
    return _read_jsonl_file(path)


def _read_jsonl_file(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError as e:
                raise MonitorError(f"{path}: dòng {i} không phải JSON hợp lệ ({e})") from e
            if not obj.get("metric") or "value" not in obj:
                raise MonitorError(f"{path}: dòng {i} thiếu 'metric' hoặc 'value'")
            rows.append({"ts": obj.get("ts"), "metric": obj["metric"], "value": obj["value"],
                         "n": obj.get("n"), "labels": obj.get("labels") or {}})
    return rows


def _read_csv(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "metric" not in reader.fieldnames or "value" not in reader.fieldnames:
            raise MonitorError(f"{path}: CSV cần header có 'metric' và 'value'")
        known = {"ts", "metric", "value", "n"}
        for row in reader:
            if not (row.get("metric") or "").strip():
                continue
            labels = {k: row[k] for k in reader.fieldnames if k not in known and row.get(k) not in (None, "")}
            rows.append({"ts": row.get("ts") or None, "metric": row["metric"], "value": row["value"],
                         "n": row.get("n") or None, "labels": labels})
    return rows


def cmd_drift(a):
    if not a.ref or not a.cur:
        raise MonitorError("drift cần cả --ref và --cur")
    ref, cur, dtype = _load_drift_pair(a.ref, a.cur, a.type)
    if dtype == "categorical":
        psi = psi_categorical(ref, cur)
        ks_d, ks_p = None, None
    else:
        psi = psi_numeric(ref, cur, a.bins)
        ks_d, ks_p = ks_two_sample([float(v) for v in ref], [float(v) for v in cur])
    level = "ổn định" if psi < 0.1 else ("cảnh báo" if psi < 0.25 else "dịch chuyển lớn")
    out = {"ref_n": len(ref), "cur_n": len(cur), "type": dtype, "psi": psi, "psi_level": level,
           "ks_d": ks_d, "ks_p": ks_p}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"=== Drift ({dtype}) ===")
        print(f"PSI = {psi:.4f}  ({level}; ngưỡng: <0.1 ổn định, 0.1-0.25 cảnh báo, >0.25 dịch chuyển lớn)")
        if ks_d is not None:
            print(f"KS  = D={ks_d:.4f}, p={ks_p:.4g}  (D lớn / p nhỏ = phân bố khác biệt)")
        print(f"ref_n={len(ref)} cur_n={len(cur)}")
    return 0


def _load_drift_pair(ref_path, cur_path, forced_type):
    def load(path):
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            vals = data.get("values")
            if not isinstance(vals, list):
                raise MonitorError(f"{path}: object cần trường 'values' là list")
            return vals, data.get("type")
        if isinstance(data, list):
            return data, None
        raise MonitorError(f"{path}: cần list hoặc object {{'type','values'}}")
    ref, rt = load(ref_path)
    cur, ct = load(cur_path)
    dtype = forced_type or rt or ct
    if dtype is None:
        dtype = "numeric" if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in ref + cur) else "categorical"
    if dtype not in ("numeric", "categorical"):
        raise MonitorError(f"type phải là numeric|categorical, nhận được: {dtype!r}")
    return ref, cur, dtype


def cmd_check(a):
    try:
        report = run_check(a.run_dir, a.now, a.policy)
    except MonitorError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 3
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_check_table(report)
    return report["exit_level"]


def cmd_list(a):
    reg = statefile.read_jsonl(incidents_registry(a.run_dir))
    events = statefile.read_jsonl(incident_events_path(a.run_dir))
    last_event = {}
    for ev in events:
        if ev.get("id"):
            last_event[ev["id"]] = ev.get("action")
    items = []
    for r in reg:
        status = last_event.get(r.get("id")) or r.get("status") or "open"
        items.append({**r, "status": status})
    if a.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        if not items:
            print("Không có incident giám sát.")
        for it in items:
            print(f"{it['id']:<45} {it['status']:<9} {it['tier']:<9} {it['metric']} value={it['value']} window={it.get('window')}")
    return 0


def _incident_match_keys(rec):
    """Cac cach nguoi dung co the goi 1 incident: id day du, ten file, hoac stem."""
    keys = set()
    if rec.get("id"):
        keys.add(str(rec["id"]))
    path = rec.get("path")
    if path:
        path = str(path)
        keys.add(path)
        base = os.path.basename(path)
        keys.add(base)
        stem, _ext = os.path.splitext(base)
        if stem:
            keys.add(stem)
    return keys


def _open_incidents(run_dir, reg):
    """Incident chua co su kien dismiss/resolve (dang mo)."""
    events = statefile.read_jsonl(incident_events_path(run_dir))
    closed = {ev.get("id") for ev in events if ev.get("action") in ("dismiss", "resolve")}
    return [r for r in reg if r.get("id") not in closed]


def _require_incident(run_dir, incident):
    """Tra ve ban ghi incident theo id day du | ten file | stem; loi ro neu khong thay."""
    reg = statefile.read_jsonl(incidents_registry(run_dir))
    found = [r for r in reg if incident in _incident_match_keys(r)]
    if found:
        return found[-1]
    opening = _open_incidents(run_dir, reg)
    if opening:
        listing = "; ".join(
            "%s (%s)" % (r.get("id"), os.path.basename(str(r.get("path") or ""))) for r in opening)
    else:
        listing = "(không có incident đang mở)"
    raise MonitorError(
        f"không tìm thấy incident '{incident}'. Incident đang mở: {listing}")


def _append_incident_event(run_dir, incident, action, reason, actor):
    rec = {"id": incident, "action": action, "reason": reason, "actor": actor or "agent", "ts": utcnow()}
    statefile.append_jsonl(incident_events_path(run_dir), rec)
    reg = _require_incident(run_dir, incident)
    path = reg.get("path")
    if path and os.path.exists(path):
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"- {action.upper()} lúc {rec['ts']} bởi {rec['actor']}: {reason or '-'}\n")
    return rec


def cmd_dismiss(a):
    rec = _require_incident(a.run_dir, a.incident)
    if not a.reason or not a.reason.strip():
        raise MonitorError("dismiss BẮT BUỘC có lý do (--reason)")
    _append_incident_event(a.run_dir, rec["id"], "dismiss", a.reason.strip(), a.actor)
    print(f"dismissed {rec['id']} (lý do ghi append-only)")
    return 0


def cmd_resolve(a):
    rec = _require_incident(a.run_dir, a.incident)
    _append_incident_event(a.run_dir, rec["id"], "resolve", (a.reason or "").strip(), a.actor)
    print(f"resolved {rec['id']}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    p_rec = sp.add_parser("record", help="Ghi 1 bản ghi metric (append-only)")
    p_rec.add_argument("run_dir")
    p_rec.add_argument("--metric", required=True)
    p_rec.add_argument("--value", required=True)
    p_rec.add_argument("--n", type=int)
    p_rec.add_argument("--label", action="append", help="k=v (lặp lại được), vd. --label model_version=rec-v1 --label slice=hw")
    p_rec.add_argument("--now", help="mốc thời gian ISO cho bản ghi (cho test)")

    p_ing = sp.add_parser("ingest", help="Nhập nhiều bản ghi từ file JSONL/CSV")
    p_ing.add_argument("run_dir")
    p_ing.add_argument("file")

    p_dr = sp.add_parser("drift", help="PSI/KS giữa ref.json và cur.json (đặc trưng số/phân loại)")
    p_dr.add_argument("--ref")
    p_dr.add_argument("--cur")
    p_dr.add_argument("--bins", type=int, default=10)
    p_dr.add_argument("--type", choices=("numeric", "categorical"))
    p_dr.add_argument("--json", action="store_true")

    p_ch = sp.add_parser("check", help="So metrics với policy, in band + mở incident")
    p_ch.add_argument("run_dir")
    p_ch.add_argument("--now", help="mốc hiện tại ISO (cho test stale)")
    p_ch.add_argument("--policy", help="đường dẫn policy (mặc định runs/<id>/monitor_policy.json)")
    p_ch.add_argument("--json", action="store_true")

    p_ls = sp.add_parser("list", help="Liệt kê incident giám sát")
    p_ls.add_argument("run_dir")
    p_ls.add_argument("--json", action="store_true")

    p_di = sp.add_parser("dismiss", help="Đóng incident (BẮT BUỘC lý do)")
    p_di.add_argument("run_dir")
    p_di.add_argument("incident")
    p_di.add_argument("--reason", required=True)
    p_di.add_argument("--actor")

    p_re = sp.add_parser("resolve", help="Đánh dấu incident đã xử lý")
    p_re.add_argument("run_dir")
    p_re.add_argument("incident")
    p_re.add_argument("--reason")
    p_re.add_argument("--actor")

    a = ap.parse_args()
    handlers = {"record": cmd_record, "ingest": cmd_ingest, "drift": cmd_drift,
                "check": cmd_check, "list": cmd_list, "dismiss": cmd_dismiss, "resolve": cmd_resolve}
    try:
        return handlers[a.cmd](a)
    except MonitorError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
