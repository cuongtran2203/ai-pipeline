#!/usr/bin/env python3
"""Tai hien P1 (RV3): admission/quota giao dich, policy fail-closed, started migration.

Bao loi (runs/ai-pipeline-v2/reviews/review_wave_fix_p1.md):
- QUOTA GIU SAU START LOI: reserve ca wave truoc start, khong rollback khi
  worker-start that bai / thieu task map; 2 coordinator cung nhan quota.
- POLICY/USAGE HONG FAIL-OPEN: load_policy tra None cho JSON loi, start van chay.
- started.json chua migration; task_map/usage/audit chua qua statefile.
- autonomy.py append thang KG, ID trung phut -> canh lap.

Trang thai admission theo task ID: reserved -> starting -> started | failed,
luu runs/<id>/admission.json (statefile.update_json; 1 lan reserve cho TUNG
worker ngay truoc loi goi Orca; loi khong ro (mat receipt) GIU starting,
reconcile truoc retry, KHONG giai phong quota theo suy doan).
usage.tasks_started suy tu admission/started, khong cong thu cong.

Chay: python -m unittest discover -s tests -v
Stdlib only. KHONG goi Orca that (stub plan_to_herdr.run; multiprocessing stub
Orca bang file log).
"""
import io
import json
import multiprocessing
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import autonomy as au
import plan_to_herdr as p2o
import statefile


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def write_raw(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


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


def make_run_dir(tmp, plan, policy="__omit__", done=("G2",), tmap=None,
                 started=None, raw_policy=None, raw_usage=None):
    """policy='__omit__' -> khong co file (run cu); raw_policy ghi thang bytes."""
    run_dir = os.path.join(tmp, "run")
    os.makedirs(run_dir)
    write_json(os.path.join(run_dir, "plan.json"), plan)
    if raw_policy is not None:
        write_raw(os.path.join(run_dir, "autonomy_policy.json"), raw_policy)
    elif policy != "__omit__":
        write_json(os.path.join(run_dir, "autonomy_policy.json"), policy)
    if raw_usage is not None:
        write_raw(os.path.join(run_dir, "usage.json"), raw_usage)
    write_json(os.path.join(run_dir, "done.json"), list(done))
    write_json(os.path.join(run_dir, "started.json"), {} if started is None else started)
    write_json(os.path.join(run_dir, "task_map.json"), tmap if tmap is not None else
               {"_run": "run-x", "W1": "task-1", "W2": "task-2"})
    write_json(os.path.join(run_dir, "agents.json"),
               {"orchestrator": "opencode",
                "groups": {"code": ["opencode"], "debate": ["opencode"],
                           "analysis": ["opencode"]}})
    return run_dir


def run_start_ready(plan_path, run_dir, stub="ok", started_calls=None):
    """Chay plan_to_herdr --start-ready voi orca stub (khong goi Orca that).

    stub: "ok" (receipt ok + dispatchId) | "fail" (receipt ok=false, loi RO
    RANG) | "lost" (nem exception: mat receipt, loi KHONG RO).
    Tra (code, out). code la int khi SystemExit(int), 1 khi exit bang message.
    """
    old_run, old_req = p2o.run, p2o.require_herdr
    old_argv = sys.argv
    calls = started_calls if started_calls is not None else []

    def fake_run(argv):
        calls.append(argv)
        if stub == "ok":
            return {"ok": True, "result": {"dispatchId": "d-%d" % len(calls)}}
        if stub == "fail":
            return {"ok": False, "error": "worker-start that bai (stub)"}
        raise RuntimeError("mat receipt (stub)")

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


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return default


def audit_events(run_dir):
    p = os.path.join(run_dir, "audit.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _mp_worker(plan_path, run_dir, calls_log):
    """Tien trinh con (spawn-safe): stub Orca bang file log lien tien trinh."""
    import sys as _s
    _s.path.insert(0, SCRIPTS)
    import plan_to_herdr as _p2o
    import statefile as _sf

    def fake_run(argv):
        _sf.append_jsonl(calls_log, {"argv": argv})
        n = len(_sf.read_jsonl(calls_log))
        return {"ok": True, "result": {"dispatchId": "mp-%d" % n}}

    _p2o.run, _p2o.require_herdr = fake_run, lambda: "stub-herdr"
    _s.argv = ["plan_to_herdr.py", plan_path, "--run-dir", run_dir,
               "--start-ready", "--no-roster"]
    try:
        _p2o.main()
    except SystemExit as e:
        code = e.code
        _s.exit(code if isinstance(code, int) else 1)


class TestAdmissionQuota(unittest.TestCase):
    def test_cap1_wave2_chi_start_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_wave_plan()
            run_dir = make_run_dir(tmp, plan, make_policy(max_tasks=1))
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "ok", calls)
            self.assertNotEqual(code, 0, f"vuot cap phai exit != 0, out={out}")
            self.assertEqual(len(calls), 1, f"cap 1/wave 2 chi duoc start 1, out={out}")
            adm = read_json(os.path.join(run_dir, "admission.json"))
            self.assertEqual(adm["W1"]["state"], "started")
            self.assertEqual(adm["W1"]["dispatch"], "d-1")
            self.assertNotIn("W2", adm)  # bi tu choi o admission, khong giu quota
            self.assertEqual(read_json(os.path.join(run_dir, "usage.json"))["tasks_started"], 1)
            denied = [e for e in audit_events(run_dir) if e["event"] == "start_denied"]
            self.assertEqual(len(denied), 1)
            self.assertIn("W2", denied[0]["scope"])

    def test_worker_start_that_bai_giai_phong_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), make_policy(max_tasks=1))
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "fail", calls)
            self.assertNotEqual(code, 0, f"start loi phai exit != 0, out={out}")
            adm = read_json(os.path.join(run_dir, "admission.json"))
            self.assertEqual(adm["W1"]["state"], "failed")  # loi RO RANG -> giai phong
            self.assertNotIn("W1", read_json(os.path.join(run_dir, "started.json"), {}))
            self.assertEqual(read_json(os.path.join(run_dir, "usage.json"))["tasks_started"], 0)
            failed = [e for e in audit_events(run_dir) if e["event"] == "start_failed"]
            self.assertEqual(len(failed), 1)
            # Retry duoc phep (quota da giai phong), W1 start thanh cong.
            calls2 = []
            code2, out2 = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "ok", calls2)
            self.assertEqual(read_json(os.path.join(run_dir, "admission.json"))["W1"]["state"],
                             "started", f"retry phai started, out={out2}")
            self.assertEqual(len(calls2), 1, f"retry chi start W1 (W2 vuot cap), out={out2}")
            self.assertNotEqual(code2, 0)  # W2 van vuot cap

    def test_mat_receipt_giu_starting_khong_doan(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), make_policy(max_tasks=2))
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "lost", calls)
            self.assertNotEqual(code, 0, f"mat receipt phai exit != 0, out={out}")
            adm = read_json(os.path.join(run_dir, "admission.json"))
            self.assertEqual(adm["W1"]["state"], "starting")  # loi KHONG RO -> GIU
            self.assertNotIn("W1", read_json(os.path.join(run_dir, "started.json"), {}))
            self.assertEqual(read_json(os.path.join(run_dir, "usage.json"))["tasks_started"], 1)
            # Retry khi chua reconcile: KHONG start lai (tranh double-start), exit != 0.
            calls2 = []
            code2, out2 = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "ok", calls2)
            self.assertNotEqual(code2, 0, f"chua reconcile phai exit != 0, out={out2}")
            self.assertEqual(len(calls2), 0, "khong duoc start lai khi dang 'starting'")
            self.assertIn("reconcile", out2)
            self.assertEqual(read_json(os.path.join(run_dir, "admission.json"))["W1"]["state"], "starting")

    def test_thieu_task_map_khong_quota_treo(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), make_policy(max_tasks=2),
                                   tmap={"_run": "run-x", "W1": "task-1"})
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "ok", calls)
            self.assertNotEqual(code, 0, f"thieu task map phai exit != 0, out={out}")
            self.assertEqual(len(calls), 1)  # W1 ok, W2 dung TRUOC reserve
            adm = read_json(os.path.join(run_dir, "admission.json"))
            self.assertNotIn("W2", adm, "W2 thieu task map khong duoc giu quota")
            self.assertEqual(read_json(os.path.join(run_dir, "usage.json"))["tasks_started"], 1)

    def test_hai_tien_trinh_chi_mot_thang(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), make_policy(max_tasks=1))
            plan_path = os.path.join(run_dir, "plan.json")
            calls_log = os.path.join(tmp, "calls.jsonl")
            ctx = multiprocessing.get_context("spawn")
            procs = [ctx.Process(target=_mp_worker, args=(plan_path, run_dir, calls_log))
                     for _ in range(2)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(60)
            self.assertTrue(all(not p.is_alive() for p in procs), "tien trinh con treo")
            codes = sorted(p.exitcode for p in procs)
            # Ca 2 exit != 0 (W2 vuot cap 1 o ca 2 tien trinh); diem maf:
            # tong worker-start chi 1, admission chi 1 started.
            self.assertTrue(all(c != 0 for c in codes), f"exitcodes={codes}")
            self.assertEqual(len(statefile.read_jsonl(calls_log)), 1,
                             "2 coordinator tranh cap: tong chi 1 worker-start")
            adm = read_json(os.path.join(run_dir, "admission.json"))
            started_now = [tid for tid, r in adm.items()
                           if isinstance(r, dict) and r.get("state") == "started"]
            self.assertEqual(len(started_now), 1)


class TestPolicyFailClosed(unittest.TestCase):
    def test_policy_hong_dung_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), raw_policy='{"sai": ')
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "ok", calls)
            self.assertNotEqual(code, 0, f"policy hong phai DUNG start, out={out}")
            self.assertEqual(len(calls), 0, "policy hong: khong start worker nao")
            denied = [e for e in audit_events(run_dir) if e["event"] == "start_denied"]
            self.assertTrue(denied, "policy hong phai ghi audit start_denied")
            self.assertIn("corrupt", denied[0]["reason"])

    def test_policy_sai_cau_truc_dung_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), policy={"sai": "cau truc"})
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "ok", calls)
            self.assertNotEqual(code, 0, f"policy invalid phai DUNG start, out={out}")
            self.assertEqual(len(calls), 0)

    def test_policy_vang_cho_phep_va_audit_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), policy="__omit__")
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "ok", calls)
            self.assertEqual(code, 0, f"run cu khong policy van chay, out={out}")
            self.assertEqual(len(calls), 2)
            mig = [e for e in audit_events(run_dir) if e["event"] == "policy_migration"]
            self.assertEqual(len(mig), 1)

    def test_usage_hong_dung_khong_ghi_de(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = '{"tasks_started": 1, '
            run_dir = make_run_dir(tmp, make_wave_plan(), make_policy(max_tasks=2),
                                   raw_usage=bad)
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "ok", calls)
            self.assertNotEqual(code, 0, f"usage hong phai DUNG start, out={out}")
            self.assertEqual(len(calls), 0, "usage hong: khong start worker nao")
            with open(os.path.join(run_dir, "usage.json"), encoding="utf-8") as f:
                self.assertEqual(f.read(), bad, "usage hong KHONG bao gio bi ghi de")

    def test_admission_hong_dung_khong_ghi_de(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = '{"W1": {"state": '
            run_dir = make_run_dir(tmp, make_wave_plan(), make_policy(max_tasks=2))
            write_raw(os.path.join(run_dir, "admission.json"), bad)
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "ok", calls)
            self.assertNotEqual(code, 0, f"admission hong phai DUNG start, out={out}")
            self.assertEqual(len(calls), 0)
            with open(os.path.join(run_dir, "admission.json"), encoding="utf-8") as f:
                self.assertEqual(f.read(), bad, "admission hong KHONG bao gio bi ghi de")


class TestStartedMigration(unittest.TestCase):
    def test_list_sang_dict_giu_noi_dung_co_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), policy="__omit__",
                                   started=["G2", "W0"])
            calls = []
            code, out = run_start_ready(
                os.path.join(run_dir, "plan.json"), run_dir, "ok", calls)
            self.assertEqual(code, 0, f"migration xong phai chay, out={out}")
            started = read_json(os.path.join(run_dir, "started.json"))
            self.assertIsInstance(started, dict)
            self.assertIn("G2", started)  # noi dung cu giu lai
            self.assertIn("W0", started)
            self.assertIn("W1", started)  # worker moi van start
            baks = [n for n in os.listdir(run_dir) if n.startswith("started.json.bak-")]
            self.assertEqual(len(baks), 1, "migration phai de lai 1 backup")
            with open(os.path.join(run_dir, baks[0]), encoding="utf-8") as f:
                self.assertEqual(json.load(f), ["G2", "W0"])
            adm = read_json(os.path.join(run_dir, "admission.json"))
            self.assertEqual(adm["W1"]["state"], "started")
            self.assertNotIn("G2", adm, "gate khong vao admission (khong dem quota)")

    def test_derive_khong_dem_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(tmp, make_wave_plan(), policy="__omit__")
            code, _ = run_start_ready(os.path.join(run_dir, "plan.json"), run_dir, "ok", [])
            self.assertEqual(code, 0)
            # G2/G3 (gate) trong started.json nhung khong dem vao tasks_started.
            self.assertEqual(au.derive_tasks_started(run_dir), 2)


class TestAuditKG(unittest.TestCase):
    def test_hai_audit_cung_phut_khong_canh_lap(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "run")
            os.makedirs(os.path.join(run_dir, "knowledge"))
            recs = [au.append_audit(run_dir, "start", scope="W1", decision="started",
                                    reason="lan %d" % i) for i in range(2)]
            self.assertNotEqual(recs[0]["id"], recs[1]["id"], "moi audit 1 UUID")
            for r in recs:  # sync lai cung ban ghi: idempotent
                au.audit_to_kg(run_dir, r)
            edges = statefile.read_jsonl(os.path.join(run_dir, "knowledge", "edges.jsonl"))
            keys = [(e["source"], e["target"], e["type"]) for e in edges]
            self.assertEqual(len(keys), len(set(keys)), f"canh lap: {keys}")
            self.assertTrue(all(e.get("properties", {}).get("audit_id") for e in edges))
            import kg as kg_mod
            sys.path.insert(0, SCRIPTS)
            _, _, errs, _ = kg_mod.collect_validation(run_dir)
            self.assertEqual(errs, [], f"KG phai validate sach: {errs}")

    def test_kg_loi_khong_hong_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "run")
            os.makedirs(os.path.join(run_dir, "knowledge"))
            # Lam KG loi (edges.jsonl la thu muc -> append that bai) -> audit
            # van ghi + co ban ghi pending, khong de quy, khong raise.
            os.makedirs(os.path.join(run_dir, "knowledge", "edges.jsonl"))
            rec = au.append_audit(run_dir, "approve", scope="W1", decision="approve",
                                  reason="duyet")
            rows = audit_events(run_dir)
            self.assertTrue(any(r.get("id") == rec["id"] for r in rows), "audit goc phai con")
            pend = [r for r in rows if r["event"] == "kg_sync_pending"]
            self.assertEqual(len(pend), 1, "KG loi phai ghi pending sync, khong hong audit")


if __name__ == "__main__":
    unittest.main()
