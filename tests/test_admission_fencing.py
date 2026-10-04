#!/usr/bin/env python3
"""Tai hien RV4 (GA): fencing admission, create/migrate atomic, contract file, wording G3.

Bao loi (runs/ai-pipeline-v2/reviews/review_wave3.md, bang 'Loi moi / regression'):
- P1 stale reservation khong fencing: takeover theo tuoi wall-clock co the
  double-start; owner A cham gan dispatch-A len ban ghi owner-B.
- P2 migration/create check/commit roi: backup trung ten, crash mat du lieu,
  2 coordinator tao task Orca trung.
- P2 file rong/sai kieu mo gate lech contract: policy rong coi nhu missing,
  usage list coi nhu {}.
- P3 wording G3: schema/role noi G3 chi khi mode=train, code xet moi GPU.

Chay: python -m unittest discover -s tests -v
Stdlib only. KHONG goi Orca that (stub plan_to_orca.run; multiprocessing stub
Orca bang file log). Test race dung barrier file + da tien trinh (spawn).
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
import plan_to_orca as p2o
import statefile


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def write_raw(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read_raw(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def make_policy(**caps):
    return {
        "run_id": "t", "policy_version": "v9", "default_mode": "bounded_auto",
        "modes": {}, "allowed_actions": ["ask", "start", "gate"],
        "caps": caps, "warn_at": 0.8, "on_cap": "require_approval",
    }


def cpu_task(tid, deps=("G2",), extra=None):
    t = {"id": tid, "title": "worker %s" % tid, "role": "module-dev",
         "change": "lam %s" % tid, "acceptance": "xong %s" % tid,
         "mode": "evaluate-only", "resources": {"compute": "cpu"},
         "deps": list(deps)}
    if extra:
        t.update(extra)
    return t


def make_plan(*tids, deps=("G2",)):
    return {"run_id": "t", "title": "t", "report_lang": "vi",
            "tasks": [{"id": "G2", "kind": "gate", "title": "duyet plan"}]
                     + [cpu_task(t, deps) for t in tids]}


def make_run_dir(tmp, plan, tids, policy="__omit__", done=("G2",),
                 raw_policy=None, raw_usage=None, raw_admission=None,
                 raw_started=None, empty_task_map=False):
    run_dir = os.path.join(tmp, "run")
    os.makedirs(run_dir)
    write_json(os.path.join(run_dir, "plan.json"), plan)
    if raw_policy is not None:
        write_raw(os.path.join(run_dir, "autonomy_policy.json"), raw_policy)
    elif policy != "__omit__":
        write_json(os.path.join(run_dir, "autonomy_policy.json"), policy)
    if raw_usage is not None:
        write_raw(os.path.join(run_dir, "usage.json"), raw_usage)
    if raw_admission is not None:
        write_raw(os.path.join(run_dir, "admission.json"), raw_admission)
    if raw_started is not None:
        write_raw(os.path.join(run_dir, "started.json"), raw_started)
    else:
        write_json(os.path.join(run_dir, "started.json"), {})
    write_json(os.path.join(run_dir, "done.json"), list(done))
    if empty_task_map:
        write_json(os.path.join(run_dir, "task_map.json"), {})
    else:
        write_json(os.path.join(run_dir, "task_map.json"),
                   dict({"_run": "run-x"},
                        **{t: "task-%s" % t.lower() for t in tids}))
    write_json(os.path.join(run_dir, "agents.json"),
               {"orchestrator": "opencode",
                "groups": {"code": ["opencode"], "debate": ["opencode"],
                           "analysis": ["opencode"]}})
    return run_dir


def run_start_ready(plan_path, run_dir, stub="ok", started_calls=None):
    old_run, old_req = p2o.run, p2o.require_orca
    old_argv = sys.argv
    calls = started_calls if started_calls is not None else []

    def fake_run(argv):
        calls.append(argv)
        if stub == "ok":
            return {"ok": True, "result": {"dispatchId": "d-%d" % len(calls)}}
        if stub == "fail":
            return {"ok": False, "error": "worker-start that bai (stub)"}
        raise RuntimeError("mat receipt (stub)")

    p2o.run, p2o.require_orca = fake_run, lambda: "stub-orca"
    sys.argv = ["plan_to_orca.py", plan_path, "--run-dir", run_dir,
                "--start-ready", "--no-roster"]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            p2o.main()
        return 0, buf.getvalue()
    except SystemExit as e:
        return (e.code if isinstance(e.code, int) else 1), buf.getvalue()
    finally:
        p2o.run, p2o.require_orca = old_run, old_req
        sys.argv = old_argv


def run_create(plan_path, run_dir, stub_calls=None):
    """Chay --create voi Orca stub. Tra (code, out)."""
    old_run, old_req = p2o.run, p2o.require_orca
    old_argv = sys.argv
    calls = stub_calls if stub_calls is not None else []

    def fake_run(argv):
        calls.append(argv)
        if "run-create" in argv:
            return {"ok": True, "result": {"id": "run-stub-%d" % len(calls)}}
        return {"ok": True, "result": {"id": "task-stub-%d" % len(calls)}}

    p2o.run, p2o.require_orca = fake_run, lambda: "stub-orca"
    sys.argv = ["plan_to_orca.py", plan_path, "--run-dir", run_dir,
                "--create", "--no-roster"]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            p2o.main()
        return 0, buf.getvalue()
    except SystemExit as e:
        return (e.code if isinstance(e.code, int) else 1), buf.getvalue()
    finally:
        p2o.run, p2o.require_orca = old_run, old_req
        sys.argv = old_argv


def _wait_gate(path, timeout=60):
    import time as _t
    end = _t.monotonic() + timeout
    while not os.path.exists(path):
        if _t.monotonic() > end:
            raise RuntimeError("gate timeout: %s" % path)
        _t.sleep(0.02)


def _mp_start_ready(plan_path, run_dir, calls_log, gate_path):
    """Tien trinh con: doi gate roi --start-ready, Orca stub ghi file log."""
    import sys as _s
    import time as _t
    _s.path.insert(0, SCRIPTS)
    import plan_to_orca as _p2o
    import statefile as _sf
    _wait_gate(gate_path)

    def fake_run(argv):
        _sf.append_jsonl(calls_log, {"argv": argv})
        _t.sleep(0.05)
        n = len(_sf.read_jsonl(calls_log))
        return {"ok": True, "result": {"dispatchId": "mp-%d" % n}}

    _p2o.run, _p2o.require_orca = fake_run, lambda: "stub-orca"
    _s.argv = ["plan_to_orca.py", plan_path, "--run-dir", run_dir,
               "--start-ready", "--no-roster"]
    try:
        _p2o.main()
    except SystemExit as e:
        code = e.code
        _s.exit(code if isinstance(code, int) else 1)


def _mp_create(plan_path, run_dir, calls_log, gate_path):
    """Tien trinh con: doi gate roi --create, Orca stub cham de ep tranh."""
    import sys as _s
    import time as _t
    _s.path.insert(0, SCRIPTS)
    import plan_to_orca as _p2o
    import statefile as _sf
    _wait_gate(gate_path)
    import os as _os

    def fake_run(argv):
        _t.sleep(0.3)  # mo rong cua so tranh claim/commit
        _sf.append_jsonl(calls_log, {"argv": argv})
        n = len(_sf.read_jsonl(calls_log))
        if "run-create" in argv:
            return {"ok": True, "result": {"id": "run-p%d-%d" % (_os.getpid(), n)}}
        title = ""
        if "--task-title" in argv:
            title = argv[argv.index("--task-title") + 1].split()[-1]
        return {"ok": True, "result": {"id": "task-%s-p%d-%d" % (title, _os.getpid(), n)}}

    _p2o.run, _p2o.require_orca = fake_run, lambda: "stub-orca"
    _s.argv = ["plan_to_orca.py", plan_path, "--run-dir", run_dir,
               "--create", "--no-roster"]
    try:
        _p2o.main()
    except SystemExit as e:
        code = e.code
        _s.exit(code if isinstance(code, int) else 1)


def _mp_migrate(run_dir, gate_path):
    import sys as _s
    _s.path.insert(0, SCRIPTS)
    import plan_to_orca as _p2o
    _wait_gate(gate_path)
    try:
        _p2o.migrate_started(run_dir)
    except SystemExit as e:
        code = e.code
        _s.exit(code if isinstance(code, int) else 1)


def _mp_migrate_crash(run_dir):
    """Crash NGAY SAU backup, TRUOC replace (mo phong mat dien giua chung)."""
    import os as _os
    import sys as _s
    _s.path.insert(0, SCRIPTS)
    import plan_to_orca as _p2o
    import statefile as _sf
    _sf._atomic_write = lambda *a, **k: _os._exit(7)
    try:
        _p2o.migrate_started(run_dir)
    except SystemExit as e:
        code = e.code
        _s.exit(code if isinstance(code, int) else 1)
    _s.exit(0)


class TestFencingNoTakeover(unittest.TestCase):
    """P1: khong takeover theo tuoi wall-clock; mark sai owner bi tu choi."""

    def _seed_reservation(self, run_dir, owner="pid-9999-deadbeef", ts=None,
                          state="reserved", gen="gen-old-123456"):
        rec = {"state": state, "ts": ts or "2000-01-01T00:00:00Z",
               "dispatch": None, "owner": owner, "generation": gen}
        write_json(os.path.join(run_dir, "admission.json"), {"W1": rec})
        return rec

    def test_stale_reserved_khong_bi_takeover(self):
        # Owner A cham hon lease 60s rat nhieu (ts co tu nam 2000) nhung van
        # song: owner B (chinh test process) KHONG duoc nhan lai.
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"], make_policy(max_tasks=2))
            self._seed_reservation(run_dir)
            task = {"id": "W1", "role": "module-dev", "mode": "evaluate-only",
                    "resources": {"compute": "cpu"}}
            ok, reason, _ = p2o.admission_reserve(run_dir, task,
                                                 make_policy(max_tasks=2), {})
            self.assertFalse(ok, "reserved stale phai bi tu choi (khong takeover)")
            self.assertIn("GIU", reason)
            rec = p2o.admission_get(run_dir, "W1")
            self.assertEqual(rec["owner"], "pid-9999-deadbeef")
            self.assertEqual(rec["state"], "reserved")

    def test_clock_skew_khong_anh_huong(self):
        # Dong ho lech 2 chieu (qua khu/tuong lai xa, ts rac): deu khong
        # takeover, khong doi ket qua admission.
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"], make_policy(max_tasks=2))
            task = {"id": "W1", "role": "module-dev", "mode": "evaluate-only",
                    "resources": {"compute": "cpu"}}
            for ts in ("1900-01-01T00:00:00Z", "2999-12-31T23:59:59Z",
                       "khong-phai-thoi-gian", ""):
                self._seed_reservation(run_dir, ts=ts or "2000-01-01T00:00:00Z")
                ok, _, _ = p2o.admission_reserve(run_dir, task,
                                                make_policy(max_tasks=2), {})
                self.assertFalse(ok, f"ts={ts!r} van khong duoc takeover")
                rec = p2o.admission_get(run_dir, "W1")
                self.assertEqual(rec["owner"], "pid-9999-deadbeef", f"ts={ts!r}")

    def test_mark_sai_owner_bi_tu_choi(self):
        # RV4 probe: owner A cham gan dispatch-A len ban ghi owner-B phai that bai.
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"], make_policy(max_tasks=2))
            task = {"id": "W1", "role": "module-dev", "mode": "evaluate-only",
                    "resources": {"compute": "cpu"}}
            ok, _, _ = p2o.admission_reserve(run_dir, task,
                                             make_policy(max_tasks=2), {})
            self.assertTrue(ok)
            mine = p2o.admission_get(run_dir, "W1")
            self.assertIn("generation", mine)
            with self.assertRaises(p2o.AdmissionDenied):
                p2o.admission_mark(run_dir, "W1", "started",
                                   dispatch="receipt-intruder",
                                   owner="pid-1-intruder",
                                   generation="gen-fake")
            rec = p2o.admission_get(run_dir, "W1")
            self.assertEqual(rec["owner"], mine["owner"])
            self.assertIsNone(rec["dispatch"], "intruder khong duoc gan dispatch")
            self.assertEqual(rec["state"], "reserved")
            # Chu that van mark duoc.
            p2o.admission_mark(run_dir, "W1", "started", dispatch="receipt-me",
                               owner=mine["owner"], generation=mine["generation"])
            rec = p2o.admission_get(run_dir, "W1")
            self.assertEqual((rec["state"], rec["dispatch"]), ("started", "receipt-me"))

    def test_generation_lech_bi_tu_choi(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"], make_policy(max_tasks=2))
            task = {"id": "W1", "role": "module-dev", "mode": "evaluate-only",
                    "resources": {"compute": "cpu"}}
            p2o.admission_reserve(run_dir, task, make_policy(max_tasks=2), {})
            mine = p2o.admission_get(run_dir, "W1")
            with self.assertRaises(p2o.AdmissionDenied):
                p2o.admission_mark(run_dir, "W1", "starting",
                                   owner=mine["owner"], generation="gen-cu-da-het-han")
            with self.assertRaises(p2o.AdmissionDenied):
                p2o.admission_claim_starting(run_dir, "W1", "pid-1-intruder",
                                             mine["generation"])
            # Claim dung chu ngay truoc call.
            p2o.admission_claim_starting(run_dir, "W1", mine["owner"],
                                         mine["generation"])
            self.assertEqual(p2o.admission_get(run_dir, "W1")["state"], "starting")
            with self.assertRaises(p2o.AdmissionDenied):
                # Claim lan 2 (da starting) that bai: khong double-claim.
                p2o.admission_claim_starting(run_dir, "W1", mine["owner"],
                                             mine["generation"])

    def test_reconcile_mark_giu_owner_cu(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"], make_policy(max_tasks=2))
            self._seed_reservation(run_dir, state="starting")
            p2o.admission_mark(run_dir, "W1", "started")  # reconcile: khong owner
            rec = p2o.admission_get(run_dir, "W1")
            self.assertEqual(rec["state"], "started")
            self.assertEqual(rec["owner"], "pid-9999-deadbeef")
            self.assertIsNone(rec["dispatch"])


class TestSlowOwnerMultiprocess(unittest.TestCase):
    """P1: owner A cham (reserve xong xu ly lau), B tranh -> khong double-start."""

    def test_owner_cham_b_tranh_khong_double_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"], make_policy(max_tasks=2))
            plan_path = os.path.join(run_dir, "plan.json")
            task = {"id": "W1", "role": "module-dev", "mode": "evaluate-only",
                    "resources": {"compute": "cpu"}}
            # A (chinh process nay) reserve truoc.
            ok, _, _ = p2o.admission_reserve(run_dir, task,
                                             make_policy(max_tasks=2), {})
            self.assertTrue(ok)
            mine = p2o.admission_get(run_dir, "W1")
            calls_log = os.path.join(tmp, "calls.jsonl")
            gate = os.path.join(tmp, "gate")
            ctx = multiprocessing.get_context("spawn")
            b = ctx.Process(target=_mp_start_ready,
                            args=(plan_path, run_dir, calls_log, gate))
            b.start()
            write_raw(gate, "go")
            b.join(120)
            self.assertFalse(b.is_alive(), "tien trinh B treo")
            self.assertNotEqual(b.exitcode, 0, "B phai bi tu choi (W1 da co chu)")
            self.assertEqual(statefile.read_jsonl(calls_log), [],
                             "B khong duoc goi Orca (khong double-start)")
            # A cham nhung van la chu: CAS + mark thanh cong, khong bi B de.
            p2o.admission_claim_starting(run_dir, "W1", mine["owner"],
                                         mine["generation"])
            p2o.admission_mark(run_dir, "W1", "started", dispatch="receipt-A",
                               owner=mine["owner"], generation=mine["generation"])
            rec = p2o.admission_get(run_dir, "W1")
            self.assertEqual(rec["owner"], mine["owner"])
            self.assertEqual(rec["dispatch"], "receipt-A")
            self.assertEqual(rec["state"], "started")


class TestConcurrentCreate(unittest.TestCase):
    """P2: 2 coordinator --create dong thoi khong tao task Orca trung."""

    def test_create_dong_thoi_khong_trung(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1", "W2")
            plan["tasks"].append(cpu_task("W3", deps=("W1",)))
            run_dir = make_run_dir(tmp, plan, [], policy=make_policy(),
                                   empty_task_map=True)
            plan_path = os.path.join(run_dir, "plan.json")
            calls_log = os.path.join(tmp, "calls.jsonl")
            gate = os.path.join(tmp, "gate")
            ctx = multiprocessing.get_context("spawn")
            procs = [ctx.Process(target=_mp_create,
                                 args=(plan_path, run_dir, calls_log, gate))
                     for _ in range(2)]
            for p in procs:
                p.start()
            write_raw(gate, "go")
            for p in procs:
                p.join(180)
            self.assertTrue(all(not p.is_alive() for p in procs), "tien trinh treo")
            rows = statefile.read_jsonl(calls_log)
            run_creates = [r for r in rows if "run-create" in r["argv"]]
            task_creates = [r for r in rows if "task-create" in r["argv"]]
            self.assertEqual(len(run_creates), 1, f"chi 1 run-create, got={len(run_creates)}")
            titles = []
            for r in task_creates:
                argv = r["argv"]
                titles.append(argv[argv.index("--task-title") + 1].split()[-1])
            self.assertEqual(sorted(titles), ["W1", "W2", "W3"],
                             f"moi plan ID dung 1 task-create, got={titles}")
            tmap = json.loads(read_raw(os.path.join(run_dir, "task_map.json")))
            self.assertIn("_run", tmap)
            for tid in ("W1", "W2", "W3"):
                self.assertIn(tid, tmap)
                self.assertTrue(tmap[tid] and not tmap[tid].startswith("__creating__"),
                                f"{tid} phai co id that, got={tmap[tid]!r}")
            # Chay lai idempotent: khong goi Orca them.
            n_before = len(statefile.read_jsonl(calls_log))
            code, _ = run_create(plan_path, run_dir, stub_calls=[])
            self.assertEqual(code, 0, "create lai phai exit 0 (idempotent)")
            self.assertEqual(len(statefile.read_jsonl(calls_log)), n_before)


class TestMigrateAtomic(unittest.TestCase):
    """P2: migrate duoi 1 khoa, backup duy nhat, crash khong mat du lieu."""

    def test_migrate_hai_lan_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"], policy="__omit__",
                                   raw_started='["G2", "W0"]')
            first = p2o.migrate_started(run_dir)
            self.assertEqual(first, {"G2": True, "W0": True})
            baks = sorted(n for n in os.listdir(run_dir)
                          if n.startswith("started.json.bak-"))
            self.assertEqual(len(baks), 1)
            with open(os.path.join(run_dir, baks[0]), encoding="utf-8") as f:
                self.assertEqual(json.load(f), ["G2", "W0"])
            second = p2o.migrate_started(run_dir)
            self.assertEqual(second, first)
            baks2 = [n for n in os.listdir(run_dir)
                     if n.startswith("started.json.bak-")]
            self.assertEqual(len(baks2), 1, "migrate lan 2 khong them backup")

    def test_migrate_dong_thoi_mot_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"], policy="__omit__",
                                   raw_started='["G2", "W0"]')
            gate = os.path.join(tmp, "gate")
            ctx = multiprocessing.get_context("spawn")
            procs = [ctx.Process(target=_mp_migrate, args=(run_dir, gate))
                     for _ in range(2)]
            for p in procs:
                p.start()
            write_raw(gate, "go")
            for p in procs:
                p.join(60)
            self.assertTrue(all(p.exitcode == 0 for p in procs),
                            [p.exitcode for p in procs])
            with open(os.path.join(run_dir, "started.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"G2": True, "W0": True})
            baks = [n for n in os.listdir(run_dir)
                    if n.startswith("started.json.bak-")]
            self.assertEqual(len(baks), 1, f"dong thoi chi 1 backup, got={baks}")
            self.assertEqual(len(set(baks)), len(baks), "ten backup duy nhat")

    def test_migrate_crash_giua_backup_va_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            raw = '["G2", "W0"]'
            run_dir = make_run_dir(tmp, plan, ["W1"], policy="__omit__",
                                   raw_started=raw)
            ctx = multiprocessing.get_context("spawn")
            p = ctx.Process(target=_mp_migrate_crash, args=(run_dir,))
            p.start()
            p.join(60)
            self.assertEqual(p.exitcode, 7, "child phai crash sau backup")
            # File goc nguyen ven (van list hop le), backup giu du lieu goc.
            self.assertEqual(read_raw(os.path.join(run_dir, "started.json")), raw)
            baks = [n for n in os.listdir(run_dir)
                    if n.startswith("started.json.bak-")]
            self.assertEqual(len(baks), 1)
            with open(os.path.join(run_dir, baks[0]), encoding="utf-8") as f:
                self.assertEqual(json.load(f), ["G2", "W0"])
            # Migrate lai binh thuong: khong mat id nao.
            out = p2o.migrate_started(run_dir)
            self.assertEqual(out, {"G2": True, "W0": True})

    def test_started_rong_la_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"], policy="__omit__",
                                   raw_started="   \n")
            with self.assertRaises(SystemExit):
                p2o.migrate_started(run_dir)
            self.assertEqual(read_raw(os.path.join(run_dir, "started.json")), "   \n",
                             "file rong KHONG bi ghi de")


class TestContractFiles(unittest.TestCase):
    """P2: chi file KHONG TON TAI moi la legacy-missing; rong/sai kieu la corrupt."""

    def test_policy_rong_la_corrupt_dung_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1", "W2")
            run_dir = make_run_dir(tmp, plan, ["W1", "W2"], raw_policy="")
            status, _, errs = au.policy_status(run_dir)
            self.assertEqual(status, "corrupt", f"policy rong phai corrupt, errs={errs}")
            calls = []
            code, _ = run_start_ready(os.path.join(run_dir, "plan.json"),
                                      run_dir, "ok", calls)
            self.assertNotEqual(code, 0)
            self.assertEqual(calls, [])
            self.assertEqual(read_raw(os.path.join(run_dir, "autonomy_policy.json")), "")

    def test_usage_list_la_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"],
                                   policy=make_policy(max_tasks=2),
                                   raw_usage='["tasks_started"]')
            with self.assertRaises(statefile.StateCorrupt):
                au.read_usage_strict(run_dir)
            calls = []
            code, _ = run_start_ready(os.path.join(run_dir, "plan.json"),
                                      run_dir, "ok", calls)
            self.assertNotEqual(code, 0)
            self.assertEqual(calls, [])
            self.assertEqual(read_raw(os.path.join(run_dir, "usage.json")),
                             '["tasks_started"]', "usage sai kieu KHONG bi ghi de")

    def test_usage_rong_va_sai_ban_ghi(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"],
                                   policy=make_policy(max_tasks=2),
                                   raw_usage="  ")
            with self.assertRaises(statefile.StateCorrupt):
                au.read_usage_strict(run_dir)
            run_dir2 = make_run_dir(os.path.join(tmp, "t2"), plan, ["W1"],
                                    policy=make_policy(max_tasks=2),
                                    raw_usage='{"tasks_started": "nhieu"}')
            with self.assertRaises(statefile.StateCorrupt):
                au.read_usage_strict(run_dir2)
            # check_action phong thu: sai kieu -> denied (fail-closed), khong crash.
            ok, reason, _ = au.check_action(make_policy(max_tasks=2), "start",
                                            "build", {"tasks_started": "nhieu"})
            self.assertFalse(ok)
            self.assertIn("sai kieu", reason)

    def test_admission_va_started_rong_dung_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"],
                                   policy=make_policy(max_tasks=2),
                                   raw_admission="  ")
            calls = []
            code, _ = run_start_ready(os.path.join(run_dir, "plan.json"),
                                      run_dir, "ok", calls)
            self.assertNotEqual(code, 0, "admission rong phai DUNG start")
            self.assertEqual(calls, [])
            self.assertEqual(read_raw(os.path.join(run_dir, "admission.json")), "  ")
            run_dir2 = make_run_dir(os.path.join(tmp, "t2"), plan, ["W1"],
                                    policy=make_policy(max_tasks=2),
                                    raw_started="")
            calls2 = []
            code2, _ = run_start_ready(os.path.join(run_dir2, "plan.json"),
                                       run_dir2, "ok", calls2)
            self.assertNotEqual(code2, 0, "started rong phai DUNG start")
            self.assertEqual(calls2, [])

    def test_admission_ban_ghi_sai_schema_dung_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = make_plan("W1")
            run_dir = make_run_dir(tmp, plan, ["W1"],
                                   policy=make_policy(max_tasks=2),
                                   raw_admission='{"W1": {"state": "bay"}}')
            with self.assertRaises(statefile.StateCorrupt):
                au.read_admission(run_dir)
            calls = []
            code, _ = run_start_ready(os.path.join(run_dir, "plan.json"),
                                      run_dir, "ok", calls)
            self.assertNotEqual(code, 0)
            self.assertEqual(calls, [])


class TestWordingNeedsG3(unittest.TestCase):
    """P3: wording schema/role/skill khop needs_g3; project_status doi ten has_train."""

    def test_schema_noi_needs_g3(self):
        with open(os.path.join(ROOT, "schemas", "plan.schema.json"),
                  encoding="utf-8") as f:
            schema = json.load(f)
        mode_desc = schema["properties"]["tasks"]["items"]["properties"]["mode"]["description"]
        self.assertIn("needs_g3", mode_desc)
        self.assertIn("gpu", mode_desc.lower())
        compute_desc = (schema["properties"]["tasks"]["items"]["properties"]
                        ["resources"]["properties"]["compute"]["description"])
        self.assertIn("needs_g3", compute_desc)

    def test_role_va_skill_noi_needs_g3(self):
        for rel in (os.path.join("roles", "architect.md"),
                    os.path.join("roles", "module-dev.md"),
                    os.path.join("skills", "ai-pipeline-sandbox", "SKILL.md")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("needs_g3", text, f"{rel} phai noi needs_g3")

    def test_helper_khop_schema(self):
        sys.path.insert(0, SCRIPTS)
        import project_status as ps
        cases = [
            ({"id": "T1", "mode": "train"}, True),
            ({"id": "T1", "mode": "train", "resources": {"compute": "cpu"}}, True),
            ({"id": "E1", "mode": "evaluate-only",
              "resources": {"compute": "gpu"}}, True),
            ({"id": "E1", "mode": "evaluate-only",
              "resources": {"compute": "cpu"}}, False),
            ({"id": "E1", "mode": "evaluate-only"}, False),
            ({"id": "M1", "mode": "monitor", "resources": {"compute": "gpu"}}, True),
            ({"id": "R1", "mode": "retrieve-only",
              "resources": {"compute": "cpu"}}, False),
        ]
        for t, want in cases:
            self.assertEqual(p2o.needs_g3(t), want, f"plan_to_orca {t}")
            self.assertEqual(ps.task_needs_g3(t), want, f"project_status {t}")

    def test_assess_doi_ten_has_train_thanh_needs_g3(self):
        import project_status as ps
        spec = ("## Muc tieu\nHe thong hoi dap noi bo.\n"
                "## Nen tang\nChay CPU container thuong.\n"
                "## Input\nCau hoi tieng Viet cua nhan vien.\n"
                "## Output\nCau tra loi kem trich dan nguon.\n"
                "## Du lieu\n1000 doan van ban noi bo da lam sach.\n"
                "## Chi tieu\nDo chinh xac cau tra loi tren 200 cau hoi.\n")
        with tempfile.TemporaryDirectory() as tmp:
            gpu_plan = {"run_id": "t", "title": "t",
                        "tasks": [{"id": "G2", "kind": "gate", "title": "g2"},
                                  {"id": "E1", "title": "eval", "role": "module-dev",
                                   "change": "c", "acceptance": "a",
                                   "mode": "evaluate-only",
                                   "resources": {"compute": "gpu"},
                                   "deps": ["G2"]}]}
            for name in ("plan.json",):
                write_json(os.path.join(tmp, name), gpu_plan)
            with open(os.path.join(tmp, "spec.md"), "w", encoding="utf-8") as f:
                f.write(spec)
            for name in ("data_analysis.md", "requirements.md", "research.md",
                         "proposal.md", "critique.md", "architecture.md"):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    f.write("noi dung\n")
            write_json(os.path.join(tmp, "done.json"), ["G1", "G2"])
            res = ps.assess(tmp)
            self.assertTrue(res["evidence"]["needs_g3"])
            self.assertEqual(res["evidence"]["has_train"],
                             res["evidence"]["needs_g3"],
                             "alias cu has_train giu tuong thich nguoc")
            self.assertIn("G3", res["blocked_on_human"])


if __name__ == "__main__":
    unittest.main()
