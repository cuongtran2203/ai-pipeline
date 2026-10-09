#!/usr/bin/env python3
"""Cong 3 tai lieu round (require_round_docs): optimize.record, optimize.next --apply, project_status."""
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, HERE)
import optimize  # noqa: E402
import project_status  # noqa: E402
import round_docs  # noqa: E402
from test_optimize import tmp_run, write_policy, write_eval, write_diag, write_plan, write_agents  # noqa: E402


def fill_docs(round_dir):
    """3 file day du, khong con placeholder."""
    for name in round_docs.DOCS:
        with io.open(os.path.join(round_dir, name), "w", encoding="utf-8") as f:
            f.write("<html><body>da dien</body></html>")


def make_run(testcase, **policy):
    run = tmp_run(testcase)
    write_policy(run, **policy)
    write_eval(run, "round-01-baseline", {"Ngay": (54, 60)})
    write_diag(run, "Ngay", "MODEL")
    write_plan(run)
    write_agents(run)
    return run


def round_dir(run):
    return os.path.join(run, "reports", "round-01-baseline")


def go_and_record(run):
    optimize.apply_next(run, optimize.decide_next(run))
    return optimize.record_round(run, 1, gpu_hours=0.5)


class RecordGateTests(unittest.TestCase):
    def test_policy_cu_thieu_khoa_van_qua(self):
        run = make_run(self)
        go_and_record(run)  # khong co 3 file, van record duoc (hanh vi cu)

    def test_khoa_false_van_qua(self):
        go_and_record(make_run(self, require_round_docs=False))

    def test_bat_thieu_file_bi_tu_choi(self):
        run = make_run(self, require_round_docs=True)
        optimize.apply_next(run, optimize.decide_next(run))
        with self.assertRaises(optimize.OptimizeError) as cm:
            optimize.record_round(run, 1, gpu_hours=0.5)
        msg = str(cm.exception)
        for name in round_docs.DOCS:
            self.assertIn(name, msg)
        self.assertIn("round_docs.py init", msg)
        self.assertIn("round_docs.py check", msg)

    def test_bat_con_placeholder_bi_tu_choi(self):
        run = make_run(self, require_round_docs=True)
        round_docs.init(round_dir(run), {})
        optimize.apply_next(run, optimize.decide_next(run))
        with self.assertRaises(optimize.OptimizeError) as cm:
            optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertIn("placeholder", str(cm.exception))

    def test_bat_thieu_mot_file_bi_tu_choi(self):
        run = make_run(self, require_round_docs=True)
        fill_docs(round_dir(run))
        os.remove(os.path.join(round_dir(run), "method_report.html"))
        optimize.apply_next(run, optimize.decide_next(run))
        with self.assertRaises(optimize.OptimizeError) as cm:
            optimize.record_round(run, 1, gpu_hours=0.5)
        self.assertIn("thieu method_report.html", str(cm.exception))

    def test_bat_du_file_qua(self):
        run = make_run(self, require_round_docs=True)
        fill_docs(round_dir(run))
        go_and_record(run)

    def test_khoa_sai_kieu_bi_tu_choi(self):
        for bad in ("true", 1, 0, [], {}):
            errs = optimize.validate_policy(dict(self._pol(), require_round_docs=bad))
            self.assertTrue(any("require_round_docs" in e for e in errs), bad)
        self.assertEqual(optimize.validate_policy(dict(self._pol(), require_round_docs=True)), [])
        self.assertEqual(optimize.validate_policy(self._pol()), [])
        run = make_run(self, require_round_docs="true")
        with self.assertRaises(optimize.OptimizeError):
            optimize.decide_next(run)

    @staticmethod
    def _pol():
        return {"max_rounds": 3, "patience": 2, "budget": {}, "allowed_actions": ["retrain"],
                "error_analysis_split": "val", "targets": {}}

    def test_template_va_default_bat_khoa(self):
        tpl = json.load(io.open(os.path.join(ROOT, "templates", "optimize_policy.template.json"), encoding="utf-8"))
        self.assertIs(tpl.get("require_round_docs"), True)
        self.assertIs(optimize.default_policy()["require_round_docs"], True)
        sch = json.load(io.open(os.path.join(ROOT, "schemas", "optimize_policy.schema.json"), encoding="utf-8"))
        self.assertEqual(sch["properties"]["require_round_docs"]["type"], "boolean")


class RoundTaskTests(unittest.TestCase):
    def _eval_task(self, run):
        dec = optimize.decide_next(run)
        return [t for t in dec["tasks"] if t["id"].endswith("-eval")][0]

    def test_bat_task_round_liet_ke_3_file(self):
        t = self._eval_task(make_run(self, require_round_docs=True))
        for name in round_docs.DOCS:
            self.assertTrue(any(o.endswith(name) for o in t["outputs"]), name)
        self.assertIn("round_docs.py check", t["acceptance"])

    def test_tat_task_round_nhu_cu(self):
        t = self._eval_task(make_run(self))
        self.assertEqual(len(t["outputs"]), 3)
        self.assertNotIn("round_docs", t["acceptance"])


class StatusTests(unittest.TestCase):
    def test_policy_cu_khong_doi(self):
        run = make_run(self)
        self.assertEqual(project_status.round_docs_gap(run), (False, {}))

    def test_bat_round_thieu_la_thieu(self):
        run = make_run(self, require_round_docs=True)
        req, gap = project_status.round_docs_gap(run)
        self.assertTrue(req)
        self.assertIn("round-01-baseline", gap)
        fill_docs(round_dir(run))
        self.assertEqual(project_status.round_docs_gap(run), (True, {}))

    def test_assess_goi_y_tieng_viet(self):
        run = make_run(self, require_round_docs=True)
        rep = project_status.assess(run)
        self.assertIn("round_docs", rep["blocked_on_human"])
        self.assertTrue(any("round_docs.py init" in a for a in rep["next_actions"]))
        fill_docs(round_dir(run))
        rep = project_status.assess(run)
        self.assertNotIn("round_docs", rep["blocked_on_human"])


if __name__ == "__main__":
    unittest.main()
