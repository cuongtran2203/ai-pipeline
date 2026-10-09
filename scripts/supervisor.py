#!/usr/bin/env python3
"""Supervisor: giam sat worker + budget theo autonomy policy (chi doc + thong bao).

Doc policy (autonomy_policy.json), bo dem tich luy (usage.json) va snapshot
trang thai worker (workers.json) de phat hien:
  - worker im lang qua han (now - last_heartbeat > stale_worker_minutes)
  - gan/vuot cap (task/tien API/thoi gian/GPU-gio/token)
  - worker failed

Chi IN canh bao co dong + goi y hanh dong theo escalation policy; de xuat
pause/kill o muc Run duoi dang lenh herdr nhung CAN nguoi xac nhan.
KHONG tu kill/retry worker, KHONG thuc thi bat cu lenh herdr nao.

workers.json (snapshot tu `scripts/herdr_snapshot.py`):
  {"now": "2026-10-04T12:00:00Z",
   "workers": [{"id": "w1", "task": "M1", "state": "running",
                "last_heartbeat": "2026-10-04T11:30:00Z"}]}
state: running|failed|done. Thieu file = khong co du lieu runtime (bao, khong fail).

Exit code: 0 = on, 1 = canh bao, 2 = nghiem trong (can nguoi can thiep).
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autonomy as au


def parse_ts(s):
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


def load_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            v = json.load(f)
        return v if v is not None else default
    except (OSError, ValueError):
        return default


def supervise(run_dir, policy=None, usage=None, snap=None, now=None, stale_override=None):
    """Tra ve (level, lines, suggestions): level 0=on, 1=canh bao, 2=nghiem trong."""
    lines, sugs = [], []
    level = 0

    def bump(lv, msg, sug=None):
        nonlocal level
        level = max(level, lv)
        lines.append(("CANH BAO" if lv == 1 else "NGHIEM TRONG") + f": {msg}")
        if sug:
            sugs.append(sug)

    if policy is None:
        lines.append("khong co autonomy_policy.json: giam sat toi thieu (khong cap de doi chieu)")
        stale_min = stale_override or 15
    else:
        stale_min = stale_override or policy.get("stale_worker_minutes", 15)
        caps = policy.get("caps") or {}
        warn_at = policy.get("warn_at", 0.8)
        usage = usage or {}
        for cap_key in au.CAP_KEYS:
            cap = caps.get(cap_key)
            if cap is None:
                continue
            used = (usage or {}).get(au.USAGE_OF[cap_key], 0) or 0
            if used >= cap:
                bump(2, f"vuot tran {cap_key} ({used}/{cap}); on_cap={policy.get('on_cap')}",
                     "hoi nguoi ngay (approve/retarget/stop); lenh goi y: "
                     "python scripts/autonomy.py approve <run_dir> --scope <task> --decision <approve|reject> --reason \"...\"")
            elif used >= warn_at * cap:
                bump(1, f"gan tran {cap_key}: {used}/{cap} (>={int(warn_at * 100)}%)",
                     "xem xet giam tai / xin cap moi truoc khi cham tran")

    if snap is None:
        lines.append("khong co workers snapshot: bo qua kiem tra heartbeat (chay voi --workers de giam sat)")
    else:
        now = now or parse_ts((snap or {}).get("now")) or dt.datetime.now(dt.timezone.utc)
        for w in (snap or {}).get("workers", []):
            wid, state = w.get("id", "?"), w.get("state", "running")
            if state == "failed":
                bump(2, f"worker {wid} (task {w.get('task', '?')}) FAILED",
                     f"kiem tra log worker {wid}, sua/retry thu cong sau khi co nguoi duyet; "
                     f"khong tu retry (xem skill ai-pipeline-autonomy)")
            elif state == "running":
                hb = parse_ts(w.get("last_heartbeat"))
                if hb is None:
                    bump(1, f"worker {wid} (task {w.get('task', '?')}) khong co heartbeat",
                         f"hoi coordinator kiem tra: herdr agent get <pane cua {wid}> (hoac python scripts/herdr_rt.py worker-list --run-dir <run_dir>)")
                elif (now - hb).total_seconds() > stale_min * 60:
                    mins = int((now - hb).total_seconds() // 60)
                    bump(2, f"worker {wid} (task {w.get('task', '?')}) im lang {mins} phut (> {stale_min})",
                         f"escalate len coordinator; neu treo that, nguoi xac nhan roi pause/kill muc Run, vd.: "
                         f"herdr pane read <pane> --source recent  (python scripts/herdr_rt.py worker-list --run-dir <run_dir>)")
    if level == 0:
        lines.append("on: khong thay worker im lang, failed hay gan/vuot cap")
    if level >= 2:
        sugs.append("Luu y: supervisor KHONG tu pause/kill/retry. Moi de xuat pause/kill Run can nguoi xac nhan truoc.")
    return level, lines, sugs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--policy", help="duong dan policy (mac dinh runs/<id>/autonomy_policy.json)")
    ap.add_argument("--usage-file", help="duong dan usage.json")
    ap.add_argument("--workers", help="duong dan workers snapshot JSON")
    ap.add_argument("--now", help="moc thoi gian hien tai (ISO, cho test): vd. 2026-10-04T12:00:00Z")
    ap.add_argument("--stale-minutes", type=int, help="ghi de stale_worker_minutes cua policy")
    a = ap.parse_args()

    policy = au.load_policy(a.run_dir, a.policy)
    if policy is not None:
        errs = au.validate_policy(policy)
        if errs:
            sys.exit("policy error: " + "; ".join(errs))
    usage = au.read_usage(a.run_dir, a.usage_file)
    snap = load_json(a.workers, None) if a.workers else None
    if a.workers and snap is None:
        sys.exit(f"workers error: khong doc duoc {a.workers}")
    now = parse_ts(a.now) if a.now else None
    level, lines, sugs = supervise(a.run_dir, policy, usage, snap, now, a.stale_minutes)
    print(f"Run: {a.run_dir}  | policy: {(policy or {}).get('policy_version', '-') if policy else '-'}")
    for l in lines:
        print("  -", l)
    if sugs:
        print("Goi y hanh dong:")
        for s in sugs:
            print("  *", s)
    return level


if __name__ == "__main__":
    sys.exit(main())
