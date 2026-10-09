#!/usr/bin/env python3
"""Tai hien P1: cap autonomy phai cong don theo TUNG worker trong wave.

Yeu cau (debate_v2_improvements.md bang B, dong 1):
- --start-ready cap quota theo tung worker truoc khi start (cong tasks_started
  ngay vao usage.json ben vung, atomic temp+replace);
- worker vuot cap bi tu choi voi audit start_denied va exit != 0;
- phan biet 'chua biet' voi 0 (canh bao 'usage unknown' cho cap tien/token/GPU
  khong co nguon do);
- policy hong/vang giu tuong thich nguoc (van chay) nhung in canh bao ro.

Chay: python -m unittest discover -s tests -v
Stdlib only. Khong goi Orca that (monkeypatch plan_to_herdr.run).
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import autonomy as au
import plan_to_herdr as p2o


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def make_policy(**caps):
    return {
        "run_id": "t", "policy_version": "v9", "default_mode": "bounded_auto",
        "modes": {}, "allowed_actions": ["ask", "start", "gate"],
        "caps": caps, "warn_at": 0.8, "on_cap": "require_approval",
    }


def make_wave_plan():
    return {
        "run_id": "t", "title": "t", "report_lang": "vi",
        "tasks": [
            {"id": "G2", "kind": "gate", "title": "duyet plan"},
            {"id": "G3", "kind": "gate", "title": "thong tin GPU"},
            {"id": "W1", "title": "worker 1", "role": "module-dev",
             "change": "lam A", "acceptance": "xong A",
             "mode": "evaluate-only", "resources": {"compute": "cpu"},
             "deps": ["G2"]},
            {"id": "W2", "title": "worker 2", "role": "module-dev",
             "change": "lam B", "acceptance": "xong B",
             "mode": "evaluate-only", "resources": {"compute": "cpu"},
             "deps": ["G2"]},
        ],
    }


def make_run_dir(tmp, plan, policy=None, done=("G2",)):
    run_dir = os.path.join(tmp, "run")
    os.makedirs(run_dir)
    write(os.path.join(run_dir, "plan.json"), plan)
    if policy is not None:
        write(os.path.join(run_dir, "autonomy_policy.json"), policy)
    write(os.path.join(run_dir, "done.json"), list(done))
    write(os.path.join(run_dir, "started.json"), {})
    write(os.path.join(run_dir, "task_map.json"),
          {"_run": "run-x", "W1": "task-1", "W2": "task-2"})
    write(os.path.join(run_dir, "agents.json"),
          {"orchestrator": "opencode",
           "groups": {"code": ["opencode"], "debate": ["opencode"],
                      "analysis": ["opencode"]}})
    return run_dir


def run_start_ready(plan_path, run_dir, started_calls):
    """Chay plan_to_herdr --start-ready voi orca stub (khong goi Orca that)."""
    old_run, old_req = p2o.run, p2o.require_herdr
    old_argv = sys.argv

    def fake_run(argv):
        started_calls.append(argv)
        return {"ok": True, "result": {"dispatchId": f"d-{len(started_calls)}"}}

    p2o.run, p2o.require_herdr = fake_run, lambda: "stub-herdr"
    sys.argv = ["plan_to_herdr.py", plan_path, "--run-dir", run_dir,
                "--start-ready", "--no-roster"]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            p2o.main()
        return 0, buf.getvalue()
    except SystemExit as e:
        return (e.code if isinstance(e.code, int) else 1), buf.getvalue()
    finally:
        p2o.run, p2o.require_herdr = old_run, old_req
        sys.argv = old_argv


def audit_events(run_dir):
    p = os.path.join(run_dir, "audit.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestReserveQuota(unittest.TestCase):
    def test_reserve_cong_don_va_ghi_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            u = au.reserve_task_quota(tmp, 1)
            self.assertEqual(u.get("tasks_started"), 1)
            u = au.reserve_task_quota(tmp, 1, usage=u)
            self.assertEqual(u.get("tasks_started"), 2)
            with open(os.path.join(tmp, "usage.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["tasks_started"], 2)
            self.assertFalse(os.path.exists(
                os.path.join(tmp, "usage.json.tmp")))

    def test_quota_moi_vong_check_thay_doi_ket_qua(self):
        # Mo phong dung luong --start-ready: check -> reserve -> check lai.
        # Vong check thu 2 phai bi tu choi (cap 1, da start 1).
        with tempfile.TemporaryDirectory() as tmp:
            p = make_policy(max_tasks=1)
            usage = au.read_usage(tmp)
            ok1, _, _ = au.check_action(p, "start", "build", usage)
            self.assertTrue(ok1)
            usage = au.reserve_task_quota(tmp, 1, usage=usage)
            ok2, reason, _ = au.check_action(p, "start", "build", usage)
            self.assertFalse(ok2, "sau khi reserve, check tiep phai bi cam")
            self.assertIn("max_tasks", reason)


class TestUnknownUsage(unittest.TestCase):
    def test_cap_tien_token_gpu_thieu_so_do_bao_unknown(self):
        p = make_policy(max_tokens=1000, max_api_cost_usd=5.0,
                        max_gpu_hours=2.0)
        ok, _, warns = au.check_action(p, "start", "build", {})
        self.assertTrue(ok)  # van cho phep (tuong thich) nhung phai canh bao
        blob = " ".join(warns)
        self.assertIn("usage unknown", blob)

    def test_co_so_do_thi_khong_bao_unknown(self):
        p = make_policy(max_tokens=1000)
        _, _, warns = au.check_action(
            p, "start", "build", {"tokens": 10})
        self.assertNotIn("usage unknown", " ".join(warns))


class TestStartReadyWave(unittest.TestCase):
    def test_cap1_wave2_chi_start_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_wave_plan()
            run_dir = make_run_dir(tmp, plan, make_policy(max_tasks=1))
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, calls)
            self.assertNotEqual(code, 0,
                                f"vuot cap phai exit != 0, out={out}")
            self.assertEqual(len(calls), 1,
                             f"cap 1/wave 2 chi duoc start 1, out={out}")
            usage = au.read_usage(run_dir)
            self.assertEqual(usage.get("tasks_started"), 1)
            denied = [e for e in audit_events(run_dir)
                      if e["event"] == "start_denied"]
            self.assertEqual(len(denied), 1)
            self.assertIn("W2", denied[0]["scope"])

    def test_run_cu_khong_policy_van_chay(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), policy=None)
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, calls)
            self.assertEqual(code, 0, f"khong policy phai chay, out={out}")
            self.assertEqual(len(calls), 2)

    def test_policy_hong_dung_start_fail_closed(self):
        # RV3: file policy CO nhung parse loi -> DUNG start (fail-closed),
        # khong con "canh bao nhung van chay". Audit start_denied ly do corrupt.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(),
                                   policy={"sai": "cau truc"})
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, calls)
            self.assertNotEqual(code, 0, f"policy invalid phai DUNG start, out={out}")
            self.assertEqual(len(calls), 0)
            denied = [e for e in audit_events(run_dir)
                      if e["event"] == "start_denied"]
            self.assertTrue(denied, "policy hong phai ghi audit start_denied")

    def test_policy_json_vo_dung_start_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), policy=None)
            with open(os.path.join(run_dir, "autonomy_policy.json"),
                      "w", encoding="utf-8") as f:
                f.write('{"caps": ')
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, calls)
            self.assertNotEqual(code, 0, f"policy corrupt phai DUNG start, out={out}")
            self.assertEqual(len(calls), 0)
            self.assertIn("corrupt", out)


if __name__ == "__main__":
    unittest.main()
