#!/usr/bin/env python3
"""Tai hien P1: G3 bi bo qua voi GPU khong train.

Yeu cau (debate_v2_improvements.md bang B, dong 2):
- needs_g3 xet ca resources.compute == gpu (moi gia tri compute nhom GPU
  trong schemas/plan.schema.json);
- validate_plan bao loi ro khi task nhu vay thieu duong phu thuoc toi G3
  (va G2);
- project_status.py dung chung helper;
- fixture: mode evaluate-only + compute gpu thieu G3 bi tu choi, co G3 thi qua;
  plan CPU-only evaluate-only khong can G3.

Chay: python -m unittest discover -s tests -v
Stdlib only.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import plan_to_orca as p2o


def gpu_eval_task(deps):
    return {"id": "E1", "title": "danh gia tren GPU", "role": "module-dev",
            "change": "chay eval", "acceptance": "co eval.json",
            "mode": "evaluate-only", "resources": {"compute": "gpu"},
            "deps": deps}


def cpu_eval_task():
    return {"id": "E1", "title": "danh gia CPU", "role": "module-dev",
            "change": "chay eval", "acceptance": "co eval.json",
            "mode": "evaluate-only", "resources": {"compute": "cpu"},
            "deps": ["G2"]}


def plan_with(*tasks, gates=("G2",)):
    ts = [{"id": g, "kind": "gate", "title": g} for g in gates]
    return {"run_id": "t", "title": "t", "tasks": ts + list(tasks)}


class TestNeedsG3(unittest.TestCase):
    def test_gpu_evaluate_can_g3(self):
        self.assertTrue(p2o.needs_g3(
            {"id": "E1", "mode": "evaluate-only",
             "resources": {"compute": "gpu"}}))

    def test_cpu_evaluate_khong_can_g3(self):
        self.assertFalse(p2o.needs_g3(
            {"id": "E1", "mode": "evaluate-only",
             "resources": {"compute": "cpu"}}))

    def test_train_cpu_van_can_g3(self):
        self.assertTrue(p2o.needs_g3(
            {"id": "T1", "mode": "train",
             "resources": {"compute": "cpu"}}))

    def test_khong_resources_giu_ngu_nghia_cu(self):
        self.assertFalse(p2o.needs_g3(
            {"id": "E1", "mode": "evaluate-only"}))
        self.assertTrue(p2o.needs_g3({"id": "T1", "mode": "train"}))


class TestValidateG3(unittest.TestCase):
    def test_gpu_thieu_g3_bi_tu_choi(self):
        plan = plan_with(gpu_eval_task(["G2"]), gates=("G2",))
        with self.assertRaises(SystemExit) as cm:
            p2o.validate_plan(plan)
        self.assertNotEqual(cm.exception.code, 0)

    def test_gpu_thieu_g3_bao_loi_ro(self):
        # G3 ton tai nhung task GPU khong co duong phu thuoc toi G3:
        # loi phai neu ro task id.
        plan = plan_with(gpu_eval_task(["G2"]), gates=("G2", "G3"))
        try:
            p2o.validate_plan(plan)
        except SystemExit as e:
            self.assertIn("G3", str(e.code))
            self.assertIn("E1", str(e.code))
        else:
            self.fail("phai exit khi GPU thieu duong phu thuoc G3")

    def test_gpu_co_g3_thi_qua(self):
        plan = plan_with(gpu_eval_task(["G2", "G3"]), gates=("G2", "G3"))
        self.assertIs(p2o.validate_plan(plan), plan)

    def test_cpu_only_khong_can_g3(self):
        plan = plan_with(cpu_eval_task(), gates=("G2",))
        self.assertIs(p2o.validate_plan(plan), plan)


class TestProjectStatusSharedHelper(unittest.TestCase):
    def test_dung_chung_helper_needs_g3(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import project_status as ps
        self.assertTrue(ps.task_needs_g3(
            {"id": "E1", "mode": "evaluate-only",
             "resources": {"compute": "gpu"}}))
        self.assertFalse(ps.task_needs_g3(
            {"id": "E1", "mode": "evaluate-only",
             "resources": {"compute": "cpu"}}))

    def test_assess_yeu_cau_g3_cho_gpu(self):
        import project_status as ps
        spec = ("## Muc tieu\nHe thong hoi dap noi bo.\n"
                "## Nen tang\nChay CPU container thuong.\n"
                "## Input\nCau hoi tieng Viet cua nhan vien.\n"
                "## Output\nCau tra loi kem trich dan nguon.\n"
                "## Du lieu\n1000 doan van ban noi bo da lam sach.\n"
                "## Chi tieu\nDo chinh xac cau tra loi tren 200 cau hoi.\n")
        with tempfile.TemporaryDirectory() as tmp:
            plan = plan_with(gpu_eval_task(["G2"]), gates=("G2",))
            with open(os.path.join(tmp, "plan.json"), "w",
                       encoding="utf-8") as f:
                json.dump(plan, f)
            with open(os.path.join(tmp, "spec.md"), "w",
                       encoding="utf-8") as f:
                f.write(spec)
            for name in ("data_analysis.md", "requirements.md",
                         "research.md", "proposal.md", "critique.md",
                         "architecture.md"):
                with open(os.path.join(tmp, name), "w",
                           encoding="utf-8") as f:
                    f.write("noi dung nghien cuu da chot\n")
            with open(os.path.join(tmp, "done.json"), "w",
                       encoding="utf-8") as f:
                json.dump(["G1", "G2"], f)
            res = ps.assess(tmp)
            self.assertIn("G3", res["blocked_on_human"])
            self.assertTrue(any("G3" in a for a in res["next_actions"]))


if __name__ == "__main__":
    unittest.main()
