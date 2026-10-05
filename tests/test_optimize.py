#!/usr/bin/env python3
"""Unittest stdlib cho scripts/optimize.py: bo dieu khien vong toi uu BAT BUOC.

Chay: python -m unittest discover -s tests -v
Moi hanh vi co test FAIL tren code cu (chua co optimize.py), PASS sau.
Run gia trong thu muc tam; KHONG ghi vao runs/ that.
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
OPTIMIZE = os.path.join(SCRIPTS, "optimize.py")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import optimize  # noqa: E402


def run_cli(*args):
    return subprocess.run([sys.executable, OPTIMIZE, *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=ROOT)


def tmp_run(testcase):
    d = tempfile.mkdtemp(prefix="opt-test-")
    testcase.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return d


def write_policy(run, **over):
    pol = {"run_id": "t", "policy_version": "v1", "approved_at_G2": True,
           "max_rounds": 3, "epsilon": 0.01, "patience": 2, "top_k": 2,
           "budget": {"gpu_hours": 8.0, "max_tasks": 10},
           "allowed_actions": ["retrain", "postprocess", "add_module",
                               "research_data", "collect_data", "relabel"],
           "error_analysis_split": "val",
           "data_sources": [], "approved_sources": [],
           "weights": {}, "targets": {"Ngay": {"metric": "accuracy", "target": 0.99}}}
    pol.update(over)
    with io.open(os.path.join(run, "optimize_policy.json"), "w", encoding="utf-8") as f:
        json.dump(pol, f, ensure_ascii=False)


def write_eval(run, dirname, items, split="val", bundle=True):
    """items: {field: (correct, total)} tren split val/OOF.

    bundle=True viet ca report.md + report.html (record yeu cau bo
    artifact day du: eval.json + report.md + report.html).
    """
    d = os.path.join(run, "reports", dirname)
    os.makedirs(d, exist_ok=True)
    rows = [{"item": f, "correct": c, "total": t, "split": split} for f, (c, t) in items.items()]
    ev = {"title": "t", "version": {"model": "m-v1", "dataset": "ds-v1"},
          "overview": {"status": "s", "method": "m", "result": "r"},
          "tables": [{"name": "T", "rows": rows}],
          "errors": [], "conclusion": {"fixes": []}}
    with io.open(os.path.join(d, "eval.json"), "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False)
    if bundle:
        with io.open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
            f.write("# Bao cao %s\n\nTONG QUAN\n\nNOI DUNG\n\nKET LUAN\n" % dirname)
        with io.open(os.path.join(d, "report.html"), "w", encoding="utf-8") as f:
            f.write("<html><body><h1>%s</h1></body></html>" % dirname)


def write_usage(run, dirname, gpu_hours):
    with io.open(os.path.join(run, "reports", dirname, "usage.json"),
                 "w", encoding="utf-8") as f:
        json.dump({"gpu_hours": gpu_hours}, f, ensure_ascii=False)


def write_diag(run, comp, verdict, actions=None):
    d = os.path.join(run, "diagnosis", comp)
    os.makedirs(d, exist_ok=True)
    doc = {"component": comp, "metric": "accuracy", "current": 0.9,
           "target": 0.99, "n": 60, "tests": [],
           "verdict": verdict, "shares": {},
           "actions": actions or [{"branch": verdict, "do": "x",
                                   "predicted_gain": 0.03, "cost": "1 task",
                                   "measure": "delta >= 0.03"}],
           "outside_playbook": False}
    with io.open(os.path.join(d, "diagnosis.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def write_plan(run, extra_tasks=()):
    tasks = [{"id": "G2", "kind": "gate", "title": "G2"},
             {"id": "G3", "kind": "gate", "title": "G3"}]
    tasks.extend(extra_tasks)
    with io.open(os.path.join(run, "plan.json"), "w", encoding="utf-8") as f:
        json.dump({"title": "t", "run_id": "t", "tasks": tasks}, f, ensure_ascii=False)


def write_agents(run):
    with io.open(os.path.join(run, "agents.json"), "w", encoding="utf-8") as f:
        json.dump({"orchestrator": "claude", "groups": {"code": ["opencode"],
                                                        "debate": ["codex"]}}, f)


class InitStatusTests(unittest.TestCase):
    def test_init_creates_policy_from_template(self):
        run = tmp_run(self)
        r = run_cli("init", run)
        self.assertEqual(r.returncode, 0, r.stderr)
        pol = json.load(io.open(os.path.join(run, "optimize_policy.json"), encoding="utf-8"))
        self.assertEqual(pol["error_analysis_split"], "val")
        self.assertIn("retrain", pol["allowed_actions"])

    def test_status_needs_baseline(self):
        run = tmp_run(self)
        write_policy(run)
        r = run_cli("status", run)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("baseline", (r.stdout + r.stderr).lower())

    def test_status_table_vietnamese(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        r = run_cli("status", run)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ngay", r.stdout)
        self.assertIn("khoang cach", r.stdout)
        rep = json.loads(run_cli("status", run, "--json").stdout)
        row = [x for x in rep["rows"] if x["field"] == "Ngay"][0]
        self.assertAlmostEqual(row["baseline"], 0.9)
        self.assertAlmostEqual(row["latest"], 0.9)
        self.assertAlmostEqual(row["gap"], 0.09)
        self.assertEqual(row["verdict"], "chua dat")


class NextDiagTests(unittest.TestCase):
    def test_missing_diag_before_action(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], "GO")
        self.assertTrue(any(t["id"].startswith("R01-") and "diag" in t["id"] for t in dec["tasks"]))
        diag = [t for t in dec["tasks"] if "diag" in t["id"]][0]
        self.assertEqual(diag["role"], "weakness-diagnostician")
        self.assertNotIn("retrain", " ".join(t["id"] for t in dec["tasks"]))

    def test_model_verdict_gives_retrain_with_predicted_gain(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        dec = optimize.decide_next(run)
        ret = [t for t in dec["tasks"] if "retrain" in t["id"]]
        self.assertTrue(ret)
        self.assertGreater(ret[0]["predicted_gain"], 0)
        self.assertIn("predicted_gain", ret[0]["acceptance"])
        self.assertTrue(any(t["id"] == "R01-eval" for t in dec["tasks"]))
        ids = [t["id"] for t in dec["tasks"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_structure_needs_research_before_build_with_gate(self):
        run = tmp_run(self)
        write_policy(run, data_sources=["mnist-handwritten"],
                     targets={"Chu so": {"metric": "accuracy", "target": 0.99}})
        write_eval(run, "round-01-baseline", {"Chu so": (50, 60)})
        write_diag(run, "Chu so", "STRUCTURE",
                   [{"branch": "STRUCTURE", "do": "research dataset chu so viet tay cong khai",
                     "predicted_gain": 0.05, "cost": "2 task", "measure": "delta"}])
        dec = optimize.decide_next(run)
        kinds = [t["id"] for t in dec["tasks"]]
        self.assertTrue(any("research" in k for k in kinds), kinds)
        self.assertTrue(any("aux" in k for k in kinds), kinds)
        ri = next(i for i, k in enumerate(kinds) if "research" in k)
        ai = next(i for i, k in enumerate(kinds) if "-aux" in k)
        self.assertLess(ri, ai)
        research = [t for t in dec["tasks"] if "research" in t["id"]][0]
        self.assertIn("duyet", research["acceptance"])


class StopRuleTests(unittest.TestCase):
    def test_success_all_targets_met(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (60, 60)})
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], optimize.STOP_SUCCESS)
        self.assertTrue(dec["stop"])
        self.assertEqual(dec["final_task"], "I-final")

    def test_stop_ceiling_below_target(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        with io.open(os.path.join(run, "ceiling.json"), "w", encoding="utf-8") as f:
            json.dump({"metric": "accuracy", "target": 0.99,
                       "estimates": [{"checkpoint": "C1", "lower": 0.90, "upper": 0.95}],
                       "decision": "retarget"}, f)
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], optimize.STOP_ASK)
        self.assertIn("ceiling", dec["reason"])

    def test_stop_objective_noise_asks_human(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "NOISE")
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], optimize.STOP_ASK)

    def test_stop_plateau(self):
        run = tmp_run(self)
        write_policy(run, epsilon=0.01, patience=2)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_eval(run, "round-02-r1", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        for n, g in ((1, 0.001), (2, 0.002)):
            optimize.statefile.append_jsonl(optimize.rounds_path(run),
                                            {"round": n, "max_gain": g, "measured_gain": g,
                                             "per_field": {}, "verdict": "bac-bo"})
        st = optimize.read_state(run)
        st["next_round"] = 3
        optimize.write_state(run, st)
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], optimize.STOP_PLATEAU)

    def test_stop_max_rounds(self):
        run = tmp_run(self)
        write_policy(run, max_rounds=2)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        st = optimize.read_state(run)
        st["next_round"] = 3
        optimize.write_state(run, st)
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], optimize.STOP_MAXROUNDS)

    def test_stop_budget(self):
        run = tmp_run(self)
        write_policy(run, budget={"gpu_hours": 8.0, "max_tasks": 2})
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run, [{"id": "R01-a-retrain", "change": "x", "acceptance": "y"},
                         {"id": "R01-eval", "change": "x", "acceptance": "y"}])
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], optimize.STOP_BUDGET)


class DisciplineTests(unittest.TestCase):
    def test_test_split_rejected(self):
        run = tmp_run(self)
        write_policy(run, error_analysis_split="test")
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        r = run_cli("next", run, "--json")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("test", (r.stdout + r.stderr).lower())

    def test_test_tables_skipped_in_history(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)}, split="test")
        with self.assertRaises(optimize.OptimizeError) as ctx:
            optimize.build_status(run)
        self.assertIn("baseline", str(ctx.exception))

    def test_never_creates_task_on_test(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        dec = optimize.decide_next(run)
        blob = json.dumps(dec["tasks"], ensure_ascii=False).lower()
        self.assertNotIn('"split": "test"', blob)
        self.assertNotIn("tren test", blob)
        self.assertNotIn("danh gia tren test", blob)


class ApplyRecordTests(unittest.TestCase):
    def _go_run(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        return run

    def test_apply_idempotent(self):
        run = self._go_run()
        dec = optimize.decide_next(run)
        first = optimize.apply_next(run, dec)
        n1 = len(json.load(io.open(os.path.join(run, "plan.json"), encoding="utf-8"))["tasks"])
        optimize.record_round(run, 1, gpu_hours=0.5)
        dec2 = optimize.decide_next(run)
        second = optimize.apply_next(run, dec2)
        tasks = json.load(io.open(os.path.join(run, "plan.json"), encoding="utf-8"))["tasks"]
        ids = [t["id"] for t in tasks]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(second["applied"], 0)
        self.assertGreater(first["applied"], 0)
        self.assertEqual(len(tasks), n1)

    def test_apply_refuses_when_previous_not_recorded(self):
        run = self._go_run()
        dec = optimize.decide_next(run)
        optimize.apply_next(run, dec)
        with self.assertRaises(optimize.OptimizeError) as ctx:
            optimize.apply_next(run, dec)
        self.assertIn("record", str(ctx.exception))

    def test_record_writes_rounds_state_and_kg_valid(self):
        run = self._go_run()
        dec = optimize.decide_next(run)
        optimize.apply_next(run, dec)
        write_eval(run, "round-02-r1", {"Ngay": (57, 60)})
        rec = optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertIn("predicted_gain", rec)
        self.assertIn("measured_gain", rec)
        self.assertAlmostEqual(rec["measured_gain"], 0.05)
        self.assertEqual(rec["verdict"], "giu")
        st = optimize.read_state(run)
        self.assertEqual(st["next_round"], 2)
        self.assertIsNone(st["pending_round"])
        v = subprocess.run([sys.executable, os.path.join(SCRIPTS, "kg.py"),
                            "validate", run],
                           capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("VALID", v.stdout)

    def test_rejected_branch_not_regenerated(self):
        run = self._go_run()
        dec = optimize.decide_next(run)
        optimize.apply_next(run, dec)
        write_eval(run, "round-02-r1", {"Ngay": (54, 60)})
        rec = optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertEqual(rec["verdict"], "bac-bo")
        dec2 = optimize.decide_next(run)
        blob = json.dumps(dec2.get("tasks", []), ensure_ascii=False)
        self.assertNotIn("R02-", blob)
        self.assertTrue(dec2["stop"] or not any("retrain" in t["id"] for t in dec2.get("tasks", [])))

    def test_success_apply_writes_ifinal(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (60, 60)})
        write_plan(run)
        write_agents(run)
        dec = optimize.decide_next(run)
        out = optimize.apply_next(run, dec)
        self.assertIn("I-final", out["task_ids"])


class MutationTests(unittest.TestCase):
    """Mutation tren ban sao tam: moi sua loi core phai bi mot test bat duoc."""

    def _mutate_src(self, old, new):
        with io.open(OPTIMIZE, encoding="utf-8") as _f:
            src = _f.read()
        self.assertIn(old, src)
        return src.replace(old, new, 1)

    def _load_mutated(self, old, new):
        d = tempfile.mkdtemp(prefix="opt-mut-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, "optimize_mutated.py")
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self._mutate_src(old, new))
        spec = importlib.util.spec_from_file_location("optimize_mutated", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _met_run(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (60, 60)})
        return run

    def test_success_mutation_detected(self):
        run = self._met_run()
        self.assertEqual(optimize.decide_next(run)["decision"], optimize.STOP_SUCCESS)
        mod = self._load_mutated('h.get("direction", "higher")) > 0:\n            unmet.append(f)',
                                      'h.get("direction", "higher")) > -999:\n            unmet.append(f)')
        self.assertNotEqual(mod.decide_next(run)["decision"], optimize.STOP_SUCCESS)

    def test_ceiling_mutation_detected(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        with io.open(os.path.join(run, "ceiling.json"), "w", encoding="utf-8") as f:
            json.dump({"estimates": [{"checkpoint": "C1", "lower": 0.9, "upper": 0.95}]}, f)
        self.assertEqual(optimize.decide_next(run)["decision"], optimize.STOP_ASK)
        mod = self._load_mutated("and float(t) > upper", "and float(t) > upper + 999")
        self.assertNotEqual(mod.decide_next(run)["decision"], optimize.STOP_ASK)

    def test_plateau_mutation_detected(self):
        run = tmp_run(self)
        write_policy(run, epsilon=0.01, patience=2)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_eval(run, "round-02-r1", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        for n in (1, 2):
            optimize.statefile.append_jsonl(optimize.rounds_path(run),
                                            {"round": n, "max_gain": 0.001,
                                             "measured_gain": 0.001, "per_field": {}, "verdict": "bac-bo"})
        st = optimize.read_state(run)
        st["next_round"] = 3
        optimize.write_state(run, st)
        self.assertEqual(optimize.decide_next(run)["decision"], optimize.STOP_PLATEAU)
        mod = self._load_mutated("if all(g < eps_ref for g in tail):", "if all(g < -1 for g in tail):")
        self.assertNotEqual(mod.decide_next(run)["decision"], optimize.STOP_PLATEAU)

    def test_maxrounds_mutation_detected(self):
        run = tmp_run(self)
        write_policy(run, max_rounds=2)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        st = optimize.read_state(run)
        st["next_round"] = 3
        optimize.write_state(run, st)
        self.assertEqual(optimize.decide_next(run)["decision"], optimize.STOP_MAXROUNDS)
        mod = self._load_mutated("if used >= max_rounds:", "if used >= max_rounds + 100:")
        self.assertNotEqual(mod.decide_next(run)["decision"], optimize.STOP_MAXROUNDS)

    def test_budget_mutation_detected(self):
        run = tmp_run(self)
        write_policy(run, budget={"gpu_hours": 8.0, "max_tasks": 1})
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run, [{"id": "R01-a-retrain", "change": "x", "acceptance": "y"}])
        self.assertEqual(optimize.decide_next(run)["decision"], optimize.STOP_BUDGET)
        mod = self._load_mutated('if budget.get("max_tasks") is not None and len(r_tasks) >= int(budget["max_tasks"]):',
                                 'if False:')
        self.assertNotEqual(mod.decide_next(run)["decision"], optimize.STOP_BUDGET)

    def test_testsplit_mutation_detected(self):
        run = tmp_run(self)
        write_policy(run, error_analysis_split="test")
        self.assertTrue(optimize.validate_policy(
            json.load(io.open(os.path.join(run, "optimize_policy.json"), encoding="utf-8"))))
        mod = self._load_mutated('ALLOWED_SPLITS = ("val", "oof")', 'ALLOWED_SPLITS = ("val", "oof", "test")')
        pol = json.load(io.open(os.path.join(run, "optimize_policy.json"), encoding="utf-8"))
        self.assertEqual(mod.validate_policy(pol), [])

    def test_reject_reuse_mutation_detected(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        write_eval(run, "round-02-r1", {"Ngay": (54, 60)})
        optimize.record_round(run, 1, gpu_hours=0.5)
        real = optimize.decide_next(run)
        mod = self._load_mutated("    if branch_key in rejected:\n        return []",
                                 "    if False:  # mutation: quen nhanh bi bac bo\n        return []")
        mutated = mod.decide_next(run)
        self.assertTrue(real["stop"] or not any("retrain" in t["id"] for t in real.get("tasks", [])))
        self.assertTrue(any("retrain" in t["id"] for t in mutated.get("tasks", [])))

    def _go_run_like(self):
        return tmp_run(self)


def write_realistic_eval(run, dirname):
    """eval.json dang THUC (copy cau truc tu timesheet-ocr): 1 bang ablation
    (hang 'nhom/field · bien the · hieu ... CI95 ...') + 1 bang metric theo field."""
    d = os.path.join(run, "reports", dirname)
    os.makedirs(d, exist_ok=True)
    abl = [
        {"item": "hw/ALL \u00b7 RC +viet tay ghep (C) \u00b7 hieu -2.2d CI95 [-4.2; -0.3] \u2192 HAI",
         "correct": 930, "total": 1005, "baseline_correct": 952, "split": "val"},
        {"item": "hw/date \u00b7 RC +viet tay ghep (C) \u00b7 hieu -4.1d CI95 [-9.0; -0.0] \u2192 HAI",
         "correct": 181, "total": 195, "baseline_correct": 189, "split": "val"},
        {"item": "hw/date \u00b7 RA +augment crop that (A) \u00b7 hieu -3.6d CI95 [-7.2; -0.9] \u2192 HAI",
         "correct": 182, "total": 195, "baseline_correct": 189, "split": "val"},
        {"item": "hw/start_time \u00b7 RC +viet tay ghep (C) \u00b7 hieu -2.0d CI95 [-5.1; +1.0]",
         "correct": 175, "total": 198, "baseline_correct": 179, "split": "val"},
        {"item": "print/company_name \u00b7 RP +in synth (P) \u00b7 hieu +0.0d CI95 [+0.0; +0.0]",
         "correct": 252, "total": 252, "baseline_correct": 252, "split": "val"},
    ]
    metric = [
        {"item": "Ngay", "correct": 61, "total": 65, "baseline_correct": 63, "split": "val"},
        {"item": "Gio bat dau", "correct": 60, "total": 66, "baseline_correct": 55, "split": "val"},
    ]
    ev = {"title": "t", "version": {"model": "m-v1", "dataset": "ds-v1"},
          "overview": {"status": "s", "method": "m", "result": "r"},
          "tables": [{"name": "rec-hw ablation (val, EM; hieu vs baseline)",
                       "metric": "exact match", "rows": abl},
                      {"name": "EM tung field tren crop GT val",
                       "metric": "exact match", "rows": metric}],
          "errors": [], "conclusion": {"fixes": []}}
    with io.open(os.path.join(d, "eval.json"), "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False)


def write_policy_declared(run, table_regex, **over):
    kw = {"metrics_source": {"table_title_regex": table_regex,
                             "field_column": "item", "value_column": "auto"},
          "targets": {}}
    kw.update(over)
    write_policy(run, **kw)


class DeclaredSourceTests(unittest.TestCase):
    def test_only_declared_table_used_ablation_ignored(self):
        run = tmp_run(self)
        write_policy_declared(run, "EM tung field",
                              targets={"Ngay": {"metric": "accuracy", "target": 0.99},
                                       "Gio bat dau": {"metric": "accuracy", "target": 0.99}})
        write_realistic_eval(run, "round-01-baseline")
        rep = optimize.build_status(run)
        got = {r["field"] for r in rep["rows"]}
        self.assertEqual(got, {"Ngay", "Gio bat dau"})
        row = next(r for r in rep["rows"] if r["field"] == "Ngay")
        self.assertAlmostEqual(row["latest"], 61 / 65)

    def test_ablation_table_grouped_to_baseline(self):
        run = tmp_run(self)
        write_policy_declared(run, "ablation",
                              targets={"hw/date": {"metric": "accuracy", "target": 0.99},
                                       "hw/start_time": {"metric": "accuracy", "target": 0.99}})
        write_realistic_eval(run, "round-01-baseline")
        rep = optimize.build_status(run)
        got = {r["field"] for r in rep["rows"]}
        self.assertIn("hw/date", got)
        self.assertIn("hw/start_time", got)
        # gop 2 hang hw/date ve baseline chung 189/195 (khong phai 181 hay 182)
        row = next(r for r in rep["rows"] if r["field"] == "hw/date")
        self.assertAlmostEqual(row["latest"], 189 / 195)
        self.assertFalse(any("hieu" in r["field"] or "CI95" in r["field"]
                             for r in rep["rows"]))

    def test_no_source_multi_table_fails_closed(self):
        run = tmp_run(self)
        write_policy(run)  # khong metrics_source, eval 2 bang khong contract
        write_realistic_eval(run, "round-01-baseline")
        with self.assertRaises(optimize.OptimizeError) as ctx:
            optimize.build_status(run)
        msg = str(ctx.exception)
        self.assertIn("metrics_source", msg)
        self.assertIn("init", msg)
        with self.assertRaises(optimize.OptimizeError):
            optimize.decide_next(run)

    def test_single_simple_table_still_works_without_source(self):
        # tuong thich nguoc: file don gian 1 bang khong can khai bao
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        rep = optimize.build_status(run)
        self.assertEqual([r["field"] for r in rep["rows"]], ["Ngay"])

    def test_init_lists_candidates_and_marks_ablation(self):
        run = tmp_run(self)
        write_realistic_eval(run, "round-03-ablation")
        r = run_cli("init", run)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rec-hw ablation", r.stdout)
        self.assertIn("EM tung field", r.stdout)
        self.assertIn("ABLATION", r.stdout)

    def test_init_metrics_table_writes_policy_then_status(self):
        run = tmp_run(self)
        write_realistic_eval(run, "round-03-ablation")
        r = run_cli("init", run, "--metrics-table", "EM tung field",
                    "--field-col", "item", "--value-col", "auto")
        self.assertEqual(r.returncode, 0, r.stderr)
        pol = json.load(io.open(os.path.join(run, "optimize_policy.json"),
                                encoding="utf-8"))
        self.assertEqual(pol["metrics_source"]["table_title_regex"], "EM tung field")
        # them target roi status chi ra field cua bang khai bao
        pol["targets"] = {"Ngay": {"metric": "accuracy", "target": 0.99},
                          "Gio bat dau": {"metric": "accuracy", "target": 0.99}}
        json.dump(pol, io.open(os.path.join(run, "optimize_policy.json"),
                               "w", encoding="utf-8"), ensure_ascii=False)
        rep = optimize.build_status(run)
        self.assertEqual({r["field"] for r in rep["rows"]}, {"Ngay", "Gio bat dau"})


class MissingTargetTests(unittest.TestCase):
    def _declared_run(self):
        run = tmp_run(self)
        write_policy_declared(run, "EM tung field")
        write_realistic_eval(run, "round-01-baseline")
        return run

    def test_missing_target_stop_ask_no_tasks(self):
        run = self._declared_run()
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], optimize.STOP_ASK)
        self.assertTrue(dec["stop"])
        self.assertEqual(dec["tasks"], [])
        self.assertIn("Ngay", dec["missing_targets"])
        rep = optimize.build_status(run)
        self.assertTrue(all(r["target"] is None for r in rep["rows"]))

    def test_default_target_applies_to_all_fields(self):
        run = self._declared_run()
        pol = json.load(io.open(os.path.join(run, "optimize_policy.json"),
                                encoding="utf-8"))
        pol["default_target"] = 0.99
        json.dump(pol, io.open(os.path.join(run, "optimize_policy.json"),
                               "w", encoding="utf-8"), ensure_ascii=False)
        rep = optimize.build_status(run)
        self.assertTrue(all(r["target"] == 0.99 for r in rep["rows"]))
        self.assertTrue(all(r["target_source"] == "default" for r in rep["rows"]))
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], "GO")
        self.assertTrue(any("diag" in t["id"] for t in dec["tasks"]))

    def test_init_autofills_default_target_from_spec(self):
        run = tmp_run(self)
        write_realistic_eval(run, "round-01-baseline")
        with io.open(os.path.join(run, "spec.md"), "w", encoding="utf-8") as f:
            f.write("# Spec\nMoi field >= 99% exact match tren tap test.\n")
        r = run_cli("init", run)
        self.assertEqual(r.returncode, 0, r.stderr)
        pol = json.load(io.open(os.path.join(run, "optimize_policy.json"),
                                encoding="utf-8"))
        self.assertAlmostEqual(pol["default_target"], 0.99)
        self.assertIn("spec.md", pol.get("default_target_source", ""))


class ShortIdTests(unittest.TestCase):
    def test_id_format_short_stable_ascii(self):
        tid = optimize._task_id(1, "Gio bat dau", "diag")
        self.assertRegex(tid, r"^R\d{2}-[a-z0-9-]{1,24}-[0-9a-f]{6}-diag$")
        self.assertTrue(tid.startswith("R01-gio-bat-dau-"))
        self.assertEqual(tid, optimize._task_id(1, "Gio bat dau", "diag"))
        self.assertNotEqual(tid, optimize._task_id(1, "Gio ket thuc", "diag"))
        self.assertNotEqual(tid, optimize._task_id(1, "Gio bat dau", "retrain"))

    def test_long_variant_item_id_still_bounded(self):
        tid = optimize._task_id(
            12, "RPA +P +augment (P,A) \u00b7 hieu -3.9d CI95 [-5.9;-1.8] \u2192 HAI",
            "postprocess")
        self.assertRegex(tid, r"^R\d{2}-[a-z0-9-]{1,24}-[0-9a-f]{6}-postprocess$")
        self.assertLessEqual(len(tid), 4 + 24 + 1 + 6 + 1 + len("postprocess"))

    def test_next_diag_ids_short_and_unique(self):
        run = tmp_run(self)
        write_policy_declared(run, "EM tung field", default_target=0.99)
        write_realistic_eval(run, "round-01-baseline")
        dec = optimize.decide_next(run)
        ids = [t["id"] for t in dec["tasks"]]
        self.assertEqual(len(ids), len(set(ids)))
        for tid in ids:
            if tid.endswith("-eval"):
                continue
            self.assertRegex(tid, r"^R\d{2}-[a-z0-9-]{1,24}-[0-9a-f]{6}-[a-z]+$")
            self.assertLessEqual(len(tid), 60)


class TotalRowTests(unittest.TestCase):
    def test_all_row_report_only_never_actionable(self):
        run = tmp_run(self)
        write_policy_declared(run, "ablation", default_target=0.99,
                              targets={"hw/ALL": {"metric": "accuracy", "target": 0.999}})
        write_realistic_eval(run, "round-01-baseline")
        rep = optimize.build_status(run)
        alls = [r for r in rep["rows"] if r["is_total"]]
        self.assertTrue(alls)
        self.assertTrue(all("chi bao cao" in r["verdict"] for r in alls))
        dec = optimize.decide_next(run)
        blob = json.dumps(dec, ensure_ascii=False).lower()
        self.assertNotIn("hw-all", blob)


class RealisticMutationTests(unittest.TestCase):
    def _mutate_src(self, old, new):
        with io.open(OPTIMIZE, encoding="utf-8") as _f:
            src = _f.read()
        self.assertIn(old, src)
        return src.replace(old, new, 1)

    def _load_mutated(self, old, new):
        d = tempfile.mkdtemp(prefix="opt-mut-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, "optimize_mutated.py")
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self._mutate_src(old, new))
        spec = importlib.util.spec_from_file_location("optimize_mutated", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _declared_run(self):
        run = tmp_run(self)
        write_policy_declared(run, "EM tung field",
                              targets={"Ngay": {"metric": "accuracy", "target": 0.99},
                                       "Gio bat dau": {"metric": "accuracy", "target": 0.99}})
        write_realistic_eval(run, "round-01-baseline")
        return run

    def test_table_filter_bypass_detected(self):
        run = self._declared_run()
        real = {r["field"] for r in optimize.build_status(run)["rows"]}
        self.assertEqual(real, {"Ngay", "Gio bat dau"})
        mod = self._load_mutated(
            'if rx and not re.search(rx, str(table.get("name") or "")):\n        return False',
            'if False:  # mutation: bo loc bang -> dung ca ablation\n        return False')
        mutated = {r["field"] for r in mod.build_status(run)["rows"]}
        self.assertNotEqual(mutated, real)
        self.assertTrue(any("hw/" in f for f in mutated))

    def test_all_becomes_actionable_detected(self):
        run = tmp_run(self)
        write_policy_declared(run, "ablation", default_target=0.99)
        write_realistic_eval(run, "round-01-baseline")
        real = optimize.decide_next(run)
        self.assertNotIn("hw-all", json.dumps(real, ensure_ascii=False).lower())
        mod = self._load_mutated('return name == "all"', 'return False  # mutation')
        mutated = mod.decide_next(run)
        self.assertIn("hw-all", json.dumps(mutated, ensure_ascii=False).lower())

    def test_missing_target_go_detected(self):
        run = tmp_run(self)
        write_policy_declared(run, "EM tung field")
        write_realistic_eval(run, "round-01-baseline")
        self.assertEqual(optimize.decide_next(run)["decision"], optimize.STOP_ASK)
        mod = self._load_mutated("    if missing_target:",
                                 "    if False:  # mutation: bo STOP thieu target")
        # dot bien bo lop STOP: lop fail-closed thu hai (guard trong unmet)
        # phai chan lai, khong duoc tra GO kem task khi thieu target
        try:
            mdec = mod.decide_next(run)
        except Exception:
            return
        self.assertTrue(mdec.get("stop") or not mdec.get("tasks"),
                        "mutation bo STOP thieu target lai sinh task: %s" % (mdec,))


class OP4SignedGainTests(unittest.TestCase):
    """P1 dau gain: suy giam (Ngay 54/60 -> 48/60) phai bac-bo, khong giu."""

    def _applied_run(self, targets=None):
        run = tmp_run(self)
        write_policy(run, epsilon=0.01,
                     targets=targets or {"Ngay": {"metric": "accuracy", "target": 0.99}})
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        return run

    def test_decline_rejected_not_kept(self):
        run = self._applied_run()
        write_eval(run, "round-02-r1", {"Ngay": (48, 60)})
        rec = optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertEqual(rec["verdict"], "bac-bo")
        self.assertLess(rec["measured_gain"], 0)
        self.assertAlmostEqual(rec["measured_gain"], 48 / 60 - 54 / 60)
        self.assertIn("epsilon", rec.get("reason", ""))

    def test_improvement_kept(self):
        run = self._applied_run()
        write_eval(run, "round-02-r1", {"Ngay": (57, 60)})
        rec = optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertEqual(rec["verdict"], "giu")
        self.assertGreater(rec["measured_gain"], 0)

    def test_harm_other_field_rejected(self):
        run = tmp_run(self)
        tg = {"A": {"metric": "accuracy", "target": 0.99},
              "B": {"metric": "accuracy", "target": 0.99}}
        write_policy(run, epsilon=0.01, targets=tg)
        write_eval(run, "round-01-baseline", {"A": (54, 60), "B": (54, 60)})
        write_diag(run, "A", "MODEL")
        write_diag(run, "B", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        write_eval(run, "round-02-r1", {"A": (57, 60), "B": (52, 60)})
        rec = optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertEqual(rec["verdict"], "bac-bo")
        self.assertIn("B", rec.get("regressed_fields", []))

    def test_max_regression_threshold(self):
        run = tmp_run(self)
        tg = {"A": {"metric": "accuracy", "target": 0.99},
              "B": {"metric": "accuracy", "target": 0.99}}
        write_policy(run, epsilon=0.01, max_regression=0.02, targets=tg)
        write_eval(run, "round-01-baseline", {"A": (54, 60), "B": (54, 60)})
        write_diag(run, "A", "MODEL")
        write_diag(run, "B", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        # B giam 1/60 (~0.0167): qua epsilon nhung duoi max_regression -> giu
        write_eval(run, "round-02-r1", {"A": (57, 60), "B": (53, 60)})
        rec = optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertEqual(rec["verdict"], "giu")

    def test_decline_counts_toward_plateau(self):
        run = tmp_run(self)
        write_policy(run, epsilon=0.01, patience=2)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        write_eval(run, "round-02-r1", {"Ngay": (48, 60)})
        optimize.record_round(run, 1, gpu_hours=0.5)
        write_eval(run, "round-03-r2", {"Ngay": (48, 60)})
        optimize.record_round(run, 2, gpu_hours=0.5)
        st = optimize.read_state(run)
        st["next_round"] = 3
        optimize.write_state(run, st)
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], optimize.STOP_PLATEAU)

    def test_negative_round_does_not_inflate_calibration(self):
        run = self._applied_run()
        write_eval(run, "round-02-r1", {"Ngay": (48, 60)})
        optimize.record_round(run, 1, gpu_hours=0.5)
        st = optimize.read_state(run)
        self.assertEqual(st.get("calibration", []), [])
        self.assertTrue(st.get("calibration_negative"))
        self.assertIsNone(
            optimize.read_rounds(run)[0].get("calibration"))


class OP4RecordIdempotentTests(unittest.TestCase):
    """P1 record idempotent + crash-safe + bo artifact + khop pending."""

    def _ready_run(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        write_eval(run, "round-02-r1", {"Ngay": (57, 60)})
        return run

    def test_double_record_single_line(self):
        run = self._ready_run()
        first = optimize.record_round(run, 1, gpu_hours=0.5)
        second = optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertTrue(second.get("already_recorded"))
        rows = optimize.read_rounds(run)
        self.assertEqual(len([r for r in rows if r.get("round") == 1]), 1)
        self.assertEqual(first["eval_digest"], second["eval_digest"])
        r = run_cli("record", run, "--round", "1", "--gpu-hours", "0.5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(optimize.read_rounds(run)), 1)

    def test_crash_between_append_and_state_recovers(self):
        run = self._ready_run()
        orig_update = optimize.statefile.update_json
        armed = {"v": True}

        def flaky(path, fn, default=None):
            if armed["v"] and str(path).endswith("state.json"):
                armed["v"] = False
                raise RuntimeError("crash gia lap giua append va state")
            return orig_update(path, fn, default=default)

        optimize.statefile.update_json = flaky
        try:
            with self.assertRaises(RuntimeError):
                optimize.record_round(run, 1, gpu_hours=0.5)
        finally:
            optimize.statefile.update_json = orig_update
        rec = optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertTrue(rec.get("already_recorded"))
        self.assertEqual(len(optimize.read_rounds(run)), 1)
        st = optimize.read_state(run)
        self.assertEqual(st["next_round"], 2)
        self.assertIsNone(st["pending_round"])

    def test_missing_report_bundle_rejected(self):
        run = self._ready_run()
        os.remove(os.path.join(run, "reports", "round-02-r1", "report.md"))
        with self.assertRaises(optimize.OptimizeError) as ctx:
            optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertIn("report.md", str(ctx.exception))

    def test_pending_mismatch_rejected(self):
        run = self._ready_run()
        with self.assertRaises(optimize.OptimizeError) as ctx:
            optimize.record_round(run, 2, gpu_hours=0.5)
        self.assertIn("pending", str(ctx.exception).lower())

    def test_kg_failure_sets_pending_flag_and_reconcile(self):
        run = self._ready_run()
        orig = optimize._sync_record_kg

        def boom(*a, **k):
            raise RuntimeError("kg hong gia lap")
        optimize._sync_record_kg = boom
        try:
            optimize.record_round(run, 1, gpu_hours=0.5)
        finally:
            optimize._sync_record_kg = orig
        st = optimize.read_state(run)
        self.assertTrue(st.get("kg_pending"))
        r = run_cli("reconcile", run)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(optimize.read_state(run).get("kg_pending", []), [])


class OP4SourceGateTests(unittest.TestCase):
    """P1 cong duyet nguon: research -> GATE (kind gate) -> build; use-check."""

    def _structure_run(self, approved=(), diag_extra=None, data_sources=("mnist-handwritten",)):
        run = tmp_run(self)
        write_policy(run, data_sources=list(data_sources),
                     approved_sources=list(approved),
                     targets={"Chu so": {"metric": "accuracy", "target": 0.99}})
        write_eval(run, "round-01-baseline", {"Chu so": (50, 60)})
        acts = [{"branch": "STRUCTURE",
                 "do": "research dataset chu so viet tay cong khai mnist-handwritten",
                 "predicted_gain": 0.05, "cost": "2 task", "measure": "delta"}]
        diag = {"component": "Chu so", "metric": "accuracy", "current": 0.83,
                "target": 0.99, "n": 60, "tests": [], "verdict": "STRUCTURE",
                "shares": {}, "actions": acts, "outside_playbook": False}
        diag.update(diag_extra or {})
        d = os.path.join(run, "diagnosis", "chuso")
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "diagnosis.json"), "w", encoding="utf-8") as f:
            json.dump(diag, f, ensure_ascii=False)
        write_plan(run)
        write_agents(run)
        return run

    def test_gate_chain_research_gate_build(self):
        run = self._structure_run()
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], "GO")
        ids = [t["id"] for t in dec["tasks"]]
        research = next(t for t in dec["tasks"] if t["id"].endswith("-research"))
        gates = [t for t in dec["tasks"] if t.get("kind") == "gate"]
        self.assertEqual(len(gates), 1)
        gate = gates[0]
        aux = next(t for t in dec["tasks"] if t["id"].endswith("-aux"))
        self.assertLess(ids.index(research["id"]), ids.index(gate["id"]))
        self.assertLess(ids.index(gate["id"]), ids.index(aux["id"]))
        self.assertIn(gate["id"], aux.get("deps", []))
        self.assertIn("use-check", aux.get("acceptance", ""))
        self.assertIn("data_provenance.py", aux.get("acceptance", ""))
        self.assertIn("KHONG tai", research.get("change", ""))
        self.assertIn("duyet", research.get("acceptance", ""))

    def test_substring_no_longer_approves(self):
        run = self._structure_run(approved=("mnist",))
        dec = optimize.decide_next(run)
        self.assertTrue(any(t.get("kind") == "gate" for t in dec["tasks"]),
                        "khop substring 'mnist' trong 'mnist-handwritten' khong duoc duyet")

    def test_exact_id_approves_skips_gate_keeps_usecheck(self):
        run = self._structure_run(approved=("ext-digits-v1",),
                                  diag_extra={"dataset_ids": ["ext-digits-v1"]},
                                  data_sources=("ext-digits-v1",))
        dec = optimize.decide_next(run)
        self.assertFalse(any(t.get("kind") == "gate" for t in dec["tasks"]))
        aux = next(t for t in dec["tasks"] if t["id"].endswith("-aux"))
        self.assertIn("ext-digits-v1", aux.get("acceptance", ""))
        self.assertIn("use-check", aux.get("acceptance", ""))

    def test_gate_plan_passes_plan_to_orca_dry_run(self):
        run = self._structure_run()
        dec = optimize.decide_next(run)
        optimize.apply_next(run, dec)
        plan_p = os.path.join(run, "plan.json")
        v = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_to_orca.py"),
             plan_p, "--run-dir", run, "--dry-run"],
            capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("GATE", v.stdout)


class OP4SplitFailClosedTests(unittest.TestCase):
    """P2 split fail-closed: thieu/khac val|oof bi tu choi."""

    def _run_with_rows(self, rows, table_split=None):
        run = tmp_run(self)
        write_policy(run)
        d = os.path.join(run, "reports", "round-01-baseline")
        os.makedirs(d, exist_ok=True)
        table = {"name": "T", "rows": rows}
        if table_split is not None:
            table["split"] = table_split
        ev = {"title": "t",
              "version": {"model": "m-v1", "dataset": "ds-v1"},
              "overview": {"status": "s", "method": "m", "result": "r"},
              "tables": [table], "errors": [], "conclusion": {"fixes": []}}
        with io.open(os.path.join(d, "eval.json"), "w", encoding="utf-8") as f:
            json.dump(ev, f, ensure_ascii=False)
        with io.open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
            f.write("# r\n")
        with io.open(os.path.join(d, "report.html"), "w", encoding="utf-8") as f:
            f.write("<html></html>")
        return run

    def test_empty_split_rejected(self):
        run = self._run_with_rows(
            [{"item": "Ngay", "correct": 54, "total": 60, "split": ""}])
        with self.assertRaises(optimize.OptimizeError) as ctx:
            optimize.build_status(run)
        self.assertIn("split", str(ctx.exception).lower())

    def test_train_split_rejected(self):
        run = self._run_with_rows(
            [{"item": "Ngay", "correct": 54, "total": 60, "split": "train"}])
        with self.assertRaises(optimize.OptimizeError) as ctx:
            optimize.build_status(run)
        self.assertIn("split", str(ctx.exception).lower())

    def test_missing_split_everywhere_rejected(self):
        run = self._run_with_rows(
            [{"item": "Ngay", "correct": 54, "total": 60}])
        with self.assertRaises(optimize.OptimizeError) as ctx:
            optimize.build_status(run)
        self.assertIn("split", str(ctx.exception).lower())

    def test_split_value_declaration_must_be_val_oof(self):
        run = tmp_run(self)
        write_policy(run, metrics_source={"table_title_regex": "T",
                                          "field_column": "item",
                                          "value_column": "auto",
                                          "split_value": "test"})
        pol = json.load(io.open(os.path.join(run, "optimize_policy.json"),
                                encoding="utf-8"))
        self.assertTrue(optimize.validate_policy(pol))

    def test_eval_contract_split_test_rejected(self):
        run = tmp_run(self)
        write_policy(run)
        d = os.path.join(run, "reports", "round-01-baseline")
        os.makedirs(d, exist_ok=True)
        ev = {"title": "t", "eval_contract": {"split": "test"},
              "tables": [{"name": "T", "rows": [
                  {"item": "Ngay", "correct": 54, "total": 60, "split": "val"}]}]}
        with io.open(os.path.join(d, "eval.json"), "w", encoding="utf-8") as f:
            json.dump(ev, f, ensure_ascii=False)
        with self.assertRaises(optimize.OptimizeError):
            optimize.build_status(run)


class OP4ApplyRaceTests(unittest.TestCase):
    """P2 --apply giao dich: 2 coordinator cung next --apply -> 1 thang."""

    def test_concurrent_apply_single_winner(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        helper = os.path.join(run, "racer.py")
        with io.open(helper, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "import subprocess, sys, os, time\n"
                "go = sys.argv[1]; out = sys.argv[2]; rundir = sys.argv[3]; opt = sys.argv[4]\n"
                "for _ in range(600):\n"
                "    if os.path.exists(go): break\n"
                "    time.sleep(0.01)\n"
                "r = subprocess.run([sys.executable, opt, 'next', rundir, '--apply', '--json'],\n"
                "                   capture_output=True, text=True, encoding='utf-8')\n"
                "open(out + '.rc', 'w', encoding='utf-8').write(str(r.returncode))\n"
                "open(out + '.json', 'w', encoding='utf-8').write(r.stdout)\n"
                "open(out + '.err', 'w', encoding='utf-8').write(r.stderr)\n")
        go = os.path.join(run, "go")
        procs = []
        for i in (1, 2):
            out = os.path.join(run, "out%d" % i)
            procs.append(subprocess.Popen(
                [sys.executable, helper, go, out, run, OPTIMIZE],
                cwd=ROOT))
        try:
            time.sleep(1.0)
            io.open(go, "w", encoding="utf-8").write("go\n")
            for p in procs:
                p.wait(timeout=120)
        finally:
            for p in procs:
                if p.poll() is None:
                    p.kill()
        applied, pending_msgs = [], []
        for i in (1, 2):
            rc = int(io.open(os.path.join(run, "out%d.rc" % i),
                             encoding="utf-8").read().strip())
            stdout = io.open(os.path.join(run, "out%d.json" % i),
                             encoding="utf-8").read()
            stderr = io.open(os.path.join(run, "out%d.err" % i),
                             encoding="utf-8").read()
            try:
                n = json.loads(stdout).get("apply", {}).get("applied", 0)
            except ValueError:
                n = 0
            if rc == 0 and n > 0:
                applied.append(n)
            else:
                pending_msgs.append(stdout + stderr)
        self.assertEqual(len(applied), 1, pending_msgs)
        self.assertTrue(any("pending" in t.lower() for t in pending_msgs))
        plan = json.load(io.open(os.path.join(run, "plan.json"), encoding="utf-8"))
        ids = [t["id"] for t in plan["tasks"]]
        self.assertEqual(len(ids), len(set(ids)))
        st = optimize.read_state(run)
        self.assertIsNotNone(st.get("pending_round"))


class OP4GpuBudgetTests(unittest.TestCase):
    """P2 ngan sach GPU that: thieu usage -> tu choi; cong so do that."""

    def _go_run(self, **over):
        run = tmp_run(self)
        write_policy(run, **over)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        write_eval(run, "round-02-r1", {"Ngay": (57, 60)})
        return run

    def test_record_refuses_without_usage_when_capped(self):
        run = self._go_run()
        with self.assertRaises(optimize.OptimizeError) as ctx:
            optimize.record_round(run, 1)
        self.assertIn("gpu", str(ctx.exception).lower())
        r = run_cli("record", run, "--round", "1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gpu", (r.stdout + r.stderr).lower())

    def test_record_accepts_cli_and_file_usage(self):
        run = self._go_run()
        rec = optimize.record_round(run, 1, gpu_hours=1.5)
        self.assertAlmostEqual(rec["gpu_hours"], 1.5)
        run2 = self._go_run()
        write_usage(run2, "round-02-r1", 2.0)
        rec2 = optimize.record_round(run2, 1)
        self.assertAlmostEqual(rec2["gpu_hours"], 2.0)

    def test_budget_sums_real_usage(self):
        run = self._go_run(budget={"gpu_hours": 1.0, "max_tasks": 10})
        optimize.record_round(run, 1, gpu_hours=1.5)
        dec = optimize.decide_next(run)
        self.assertEqual(dec["decision"], optimize.STOP_BUDGET)


class OP4CoreMutationTests(unittest.TestCase):
    """Mutation cho tung sua loi OP4: revert -> test bat duoc."""

    def _mutate_src(self, old, new):
        with io.open(OPTIMIZE, encoding="utf-8") as _f:
            src = _f.read()
        self.assertIn(old, src)
        return src.replace(old, new, 1)

    def _load_mutated(self, old, new):
        d = tempfile.mkdtemp(prefix="op4-mut-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, "optimize_mutated.py")
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self._mutate_src(old, new))
        spec = importlib.util.spec_from_file_location("optimize_mutated_op4", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _decline_run(self):
        run = tmp_run(self)
        write_policy(run, epsilon=0.01)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        write_eval(run, "round-02-r1", {"Ngay": (48, 60)})
        return run

    def test_signed_gain_mutation_detected(self):
        run = self._decline_run()
        self.assertEqual(
            optimize.record_round(run, 1, gpu_hours=0.5)["verdict"], "bac-bo")
        run2 = self._decline_run()
        mod = self._load_mutated("gains[f] = mg", "gains[f] = abs(mg)")
        self.assertEqual(
            mod.record_round(run2, 1, gpu_hours=0.5)["verdict"], "giu")

    def test_regression_check_mutation_detected(self):
        run = tmp_run(self)
        tg = {"A": {"metric": "accuracy", "target": 0.99},
              "B": {"metric": "accuracy", "target": 0.99}}
        write_policy(run, epsilon=0.01, targets=tg)
        write_eval(run, "round-01-baseline", {"A": (54, 60), "B": (54, 60)})
        write_diag(run, "A", "MODEL")
        write_diag(run, "B", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        write_eval(run, "round-02-r1", {"A": (57, 60), "B": (52, 60)})
        self.assertEqual(
            optimize.record_round(run, 1, gpu_hours=0.5)["verdict"], "bac-bo")
        run2 = tmp_run(self)
        write_policy(run2, epsilon=0.01, targets=dict(tg))
        write_eval(run2, "round-01-baseline", {"A": (54, 60), "B": (54, 60)})
        write_diag(run2, "A", "MODEL")
        write_diag(run2, "B", "MODEL")
        write_plan(run2)
        write_agents(run2)
        mod = self._load_mutated("elif regressed:", "elif False:  # mutation: bo kiem hoi quy")
        write_eval(run2, "round-02-r1", {"A": (57, 60), "B": (52, 60)})
        self.assertEqual(
            mod.record_round(run2, 1, gpu_hours=0.5)["verdict"], "giu")

    def test_idempotent_mutation_detected(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        write_eval(run, "round-02-r1", {"Ngay": (57, 60)})
        mod = self._load_mutated(
            'if existing or digest_hit:',
            'if False:  # mutation: bo kiem idempotent')
        optimize.record_round(run, 1, gpu_hours=0.5)
        mod.record_round(run, 1, gpu_hours=0.5)
        self.assertEqual(len(optimize.read_rounds(run)), 2)

    def test_exact_match_mutation_detected(self):
        mod = self._load_mutated("return set(needed) <= approved",
                                 "return True  # mutation: duyet het")
        run = tmp_run(self)
        write_policy(run, data_sources=["mnist-handwritten"],
                     approved_sources=["mnist"],
                     targets={"Chu so": {"metric": "accuracy", "target": 0.99}})
        write_eval(run, "round-01-baseline", {"Chu so": (50, 60)})
        write_diag(run, "Chu so", "STRUCTURE",
                   [{"branch": "STRUCTURE", "do": "research dataset cong khai",
                     "predicted_gain": 0.05, "cost": "2", "measure": "d"}])
        real = optimize.decide_next(run)
        mutated = mod.decide_next(run)
        self.assertTrue(any(t.get("kind") == "gate" for t in real["tasks"]))
        self.assertFalse(any(t.get("kind") == "gate" for t in mutated["tasks"]))

    def test_split_mutation_detected(self):
        run = tmp_run(self)
        write_policy(run)
        d = os.path.join(run, "reports", "round-01-baseline")
        os.makedirs(d, exist_ok=True)
        rows = [{"item": "Ngay", "correct": 54, "total": 60, "split": ""}]
        ev = {"title": "t", "tables": [{"name": "T", "rows": rows}]}
        with io.open(os.path.join(d, "eval.json"), "w", encoding="utf-8") as f:
            json.dump(ev, f, ensure_ascii=False)
        with self.assertRaises(optimize.OptimizeError):
            optimize.build_status(run)
        mod = self._load_mutated("if split not in ALLOWED_SPLITS:",
                                 "if False:  # mutation: bo kiem split")
        rep = mod.build_status(run)
        self.assertTrue(any(r["field"] == "Ngay" for r in rep["rows"]))

    def test_gpu_mutation_detected(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        optimize.apply_next(run, optimize.decide_next(run))
        write_eval(run, "round-02-r1", {"Ngay": (57, 60)})
        with self.assertRaises(optimize.OptimizeError):
            optimize.record_round(run, 1)
        run2 = tmp_run(self)
        write_policy(run2)
        write_eval(run2, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run2, "Ngay", "MODEL")
        write_plan(run2)
        write_agents(run2)
        mod = self._load_mutated("if cap is not None:",
                                 "if False:  # mutation: bo tu choi gpu")
        rec = mod.record_round(run2, 1)
        self.assertAlmostEqual(rec["gpu_hours"], 0.0)

    def test_apply_guard_mutation_detected(self):
        run = tmp_run(self)
        write_policy(run)
        write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run, "Ngay", "MODEL")
        write_plan(run)
        write_agents(run)
        dec = optimize.decide_next(run)
        optimize.apply_next(run, dec)
        with self.assertRaises(optimize.OptimizeError):
            optimize.apply_next(run, dec)
        run2 = tmp_run(self)
        write_policy(run2)
        write_eval(run2, "round-01-baseline", {"Ngay": (54, 60)})
        write_diag(run2, "Ngay", "MODEL")
        write_plan(run2)
        write_agents(run2)
        mod = self._load_mutated(
            "raise OptimizeError(\"vong %s chua `record`",
            "pass  # mutation: bo guard pending (")
        mod_dec = mod.decide_next(run2)
        mod.apply_next(run2, mod_dec)
        # vong 1 chua record nhung ep decision vong 2: ban that tu choi,
        # ban mat guard ghi de pending (mat dau vet vong chua record)
        dec2 = {"decision": "GO", "stop": False, "round": 2,
                "tasks": [{"id": "R02-fake-retrain", "title": "x",
                           "role": "module-dev", "mode": "train",
                           "resources": {"compute": "gpu"},
                           "deps": [], "agent": "auto"}]}
        with self.assertRaises(optimize.OptimizeError):
            optimize.apply_next(run, dec2)
        second = mod.apply_next(run2, dec2)
        self.assertGreater(second["applied"], 0)


if __name__ == "__main__":
    unittest.main()
