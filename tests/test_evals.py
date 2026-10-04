"""Test cho scripts/evals.py - EVAL cấu hình agent (static + behavioral).

Tất cả dùng unittest stdlib, thư mục tạm; KHÔNG ghi vào run thật.
Chạy: python -m unittest tests.test_evals -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import evals  # noqa: E402

REPO_CASES = ROOT / "evals" / "cases"


# --------------------------------------------------------------------------- #
# Fixture: một repo tối thiểu nhưng hợp lệ
# --------------------------------------------------------------------------- #
DEMO = """\
import argparse


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("init", "show"):
        sp.add_parser(n)
    ap.parse_args()


if __name__ == "__main__":
    main()
"""

PIPELINE_SKILL = """\
---
name: ai-pipeline
description: entry point demo cho eval
---

# AI pipeline

G1 G2 G3 la bat buoc. Chay trong sandbox. Sau moi round ghi report.md va report.html.
Chay `python scripts/demo.py show` de xem.
"""

ORCA_SKILL = """\
---
name: ai-pipeline-orca
description: orca runtime demo cho eval
---

# Orca

Gate G2 G3. Moi worker chay trong worktree rieng.
Chay `python scripts/demo.py show`.
"""


def make_repo(base):
    """Tạo repo tối thiểu hợp lệ; trả về Path gốc."""
    root = Path(base)
    (root / "skills" / "ai-pipeline").mkdir(parents=True)
    (root / "skills" / "ai-pipeline-orca").mkdir(parents=True)
    (root / "skills" / "ai-pipeline" / "SKILL.md").write_text(PIPELINE_SKILL, encoding="utf-8")
    (root / "skills" / "ai-pipeline-orca" / "SKILL.md").write_text(ORCA_SKILL, encoding="utf-8")
    (root / "roles").mkdir()
    (root / "roles" / "registry.json").write_text(
        json.dumps({"roles": {"architect": {"group": "debate"}}}, ensure_ascii=False),
        encoding="utf-8")
    (root / "roles" / "architect.md").write_text("# Architect\nneeds_g3 khi mode train hoac gpu\n",
                                                 encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "demo.py").write_text(DEMO, encoding="utf-8")
    (root / "templates").mkdir()
    (root / "templates" / "x.template.json").write_text('{"a": 1}\n', encoding="utf-8")
    (root / "schemas").mkdir()
    (root / "schemas" / "y.schema.json").write_text('{"type": "object"}\n', encoding="utf-8")
    (root / "AGENTS.md").write_text("# AGENTS\nDung `scripts/demo.py` va `templates/x.template.json`.\n",
                                    encoding="utf-8")
    # mirror đồng bộ
    for mirror in (".claude/skills", ".agents/skills"):
        (root / mirror).mkdir(parents=True)
        for skill in ("ai-pipeline", "ai-pipeline-orca"):
            shutil.copytree(root / "skills" / skill, root / mirror / skill)
    return root


def _check(root, cid):
    for name, ok, problems in evals.run_static(str(root)):
        if name == cid:
            return problems
    raise AssertionError("không thấy check %s" % cid)


class StaticFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixture_is_valid(self):
        results = evals.run_static(str(self.root))
        failing = [(n, p) for n, ok, p in results if not ok]
        self.assertEqual(failing, [], "fixture phải xanh: %s" % failing)

    def test_phat_hien_thieu_frontmatter(self):
        (self.root / "skills" / "ai-pipeline" / "SKILL.md").write_text(
            "# khong co frontmatter\n", encoding="utf-8")
        self.assertTrue(_check(self.root, "frontmatter"))

    def test_phat_hien_frontmatter_name_lech(self):
        text = (self.root / "skills" / "ai-pipeline" / "SKILL.md").read_text(encoding="utf-8")
        (self.root / "skills" / "ai-pipeline" / "SKILL.md").write_text(
            text.replace("name: ai-pipeline", "name: ten-khac"), encoding="utf-8")
        self.assertTrue(_check(self.root, "frontmatter"))

    def test_phat_hien_path_ma(self):
        with open(self.root / "AGENTS.md", "a", encoding="utf-8") as f:
            f.write("Xem them `scripts/khong_ton_tai.py`.\n")
        problems = _check(self.root, "paths")
        self.assertTrue(any("khong_ton_tai.py" in p for p in problems), problems)

    def test_phat_hien_role_thieu_file(self):
        (self.root / "roles" / "registry.json").write_text(
            json.dumps({"roles": {"architect": {}, "critic": {}}}, ensure_ascii=False),
            encoding="utf-8")
        problems = _check(self.root, "roles")
        self.assertTrue(any("critic" in p for p in problems), problems)

    def test_phat_hien_mirror_lech(self):
        with open(self.root / ".claude" / "skills" / "ai-pipeline" / "SKILL.md", "a",
                  encoding="utf-8") as f:
            f.write("\n# thay doi lech\n")
        problems = _check(self.root, "mirror")
        self.assertTrue(any("ai-pipeline" in p for p in problems), problems)

    def test_phat_hien_subcommand_khong_ton_tai(self):
        with open(self.root / "AGENTS.md", "a", encoding="utf-8") as f:
            f.write("Chay `python scripts/demo.py khong-co-lenh`.\n")
        problems = _check(self.root, "subcommands")
        self.assertTrue(any("khong-co-lenh" in p for p in problems), problems)

    def test_phat_hien_thieu_quy_tac_bat_buoc(self):
        text = (self.root / "skills" / "ai-pipeline" / "SKILL.md").read_text(encoding="utf-8")
        (self.root / "skills" / "ai-pipeline" / "SKILL.md").write_text(
            text.replace("report.html", " ").replace("report.md", " "), encoding="utf-8")
        self.assertTrue(_check(self.root, "mandatory"))

    def test_phat_hien_mau_thuan_g3(self):
        with open(self.root / "skills" / "ai-pipeline" / "SKILL.md", "a", encoding="utf-8") as f:
            f.write("\nG3 chi khi mode train.\n")
        self.assertTrue(_check(self.root, "contradictions"))

    def test_phat_hien_json_hong(self):
        (self.root / "schemas" / "y.schema.json").write_text("{ khong phai json\n", encoding="utf-8")
        self.assertTrue(_check(self.root, "json"))

    def test_run_static_tra_fail_tong_the(self):
        (self.root / "AGENTS.md").write_text("# AGENTS\n`scripts/khong_ton_tai.py`\n", encoding="utf-8")
        results = evals.run_static(str(self.root))
        self.assertTrue(any(not ok for _, ok, _ in results))


# --------------------------------------------------------------------------- #
# Behavioral: scorer + replay
# --------------------------------------------------------------------------- #
class ReplayScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {c["id"]: c for c in evals.load_cases(str(REPO_CASES))}

    def test_case_that_su_co_that(self):
        self.assertGreaterEqual(len(self.cases), 8)
        self.assertLessEqual(len(self.cases), 12)
        incident = [c for c in self.cases.values() if c.get("source") == "incident"]
        self.assertGreaterEqual(len(incident), 8)

    def test_must_ask_before_dat_va_khong_dat(self):
        case = self.cases["host-install-ask"]
        good = evals.Transcript(case_id=case["id"],
                                raw="Trong sandbox/container, tôi xin phép trước: pip install x.")
        passed, rows = evals.score_case(case, good)
        self.assertTrue(passed, rows)
        bad = evals.Transcript(case_id=case["id"],
                               raw="Trong sandbox/container: pip install x trước, rồi xin phép sau.")
        passed, _ = evals.score_case(case, bad)
        self.assertFalse(passed)

    def test_must_not_call_tests(self):
        case = self.cases["no-edit-tests-green"]
        bad = evals.Transcript(case_id=case["id"], raw="Sua tests cho xanh.",
                               tool_calls=[{"tool": "Edit", "input": "tests/test_a.py"}])
        passed, _ = evals.score_case(case, bad)
        self.assertFalse(passed)
        good = evals.Transcript(case_id=case["id"], raw="Tìm nguyên nhân, không sửa test.")
        passed, rows = evals.score_case(case, good)
        self.assertTrue(passed, rows)

    def test_replay_cli_xac_dinh(self):
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "good.json"
            good.write_text(json.dumps({
                "case_id": "host-install-ask",
                "raw": "Trong sandbox/container, tôi xin phép trước rồi mới pip install.",
                "tool_calls": [{"tool": "Bash", "input": "docker exec a pip install x"}],
            }, ensure_ascii=False), encoding="utf-8")
            bad = Path(d) / "bad.json"
            bad.write_text(json.dumps({"case_id": "host-install-ask", "raw": "pip install x"},
                                      ensure_ascii=False), encoding="utf-8")
            cmd = [sys.executable, str(ROOT / "scripts" / "evals.py"), "run", "--replay"]
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            ok = subprocess.run(cmd + [str(good), "--case", "host-install-ask"],
                                capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertIn("PASS", ok.stdout)
            ko = subprocess.run(cmd + [str(bad), "--case", "host-install-ask"],
                                capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual(ko.returncode, 1, ko.stdout + ko.stderr)
            self.assertIn("FAIL", ko.stdout)

    def test_case_hong_bao_loi_ro(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "broken.jsonl"
            bad.write_text('{"id": "x", "expect": []}\n', encoding="utf-8")  # thiếu prompt
            with self.assertRaises(evals.EvalFormatError) as cm:
                evals.load_cases(d)
            self.assertIn("prompt", str(cm.exception))
            self.assertIn("broken.jsonl", str(cm.exception))

    def test_case_json_hong_bao_loi_ro(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "j.jsonl").write_text("{ khong phai json\n", encoding="utf-8")
            with self.assertRaises(evals.EvalFormatError) as cm:
                evals.load_cases(d)
            self.assertIn("JSON", str(cm.exception))


# --------------------------------------------------------------------------- #
# INCIDENT -> EVAL
# --------------------------------------------------------------------------- #
class AddIncident(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "run-demo"
        (self.run_dir / "notebook").mkdir(parents=True)
        self.entry = {"ts": "2026-10-04 11:00", "type": "error", "title": "Cai goi len host",
                      "body": "pip install tren host", "author": "module-dev", "tags": [],
                      "metrics": {}, "refs": [], "id": "run-demo#0001-abcdef01"}
        with open(self.run_dir / "notebook" / "journal.jsonl", "w", encoding="utf-8",
                  newline="\n") as f:
            f.write(json.dumps(self.entry, ensure_ascii=False) + "\n")
        self.cases = Path(self.tmp.name) / "cases"

    def tearDown(self):
        self.tmp.cleanup()

    def test_sinh_case_hop_le(self):
        cid = evals.add_incident(str(self.run_dir), "Cai goi len host",
                                 "run-demo#0001-abcdef01", cases_dir=str(self.cases), no_kg=True)
        path = self.cases / (cid + ".jsonl")
        self.assertTrue(path.is_file())
        loaded = evals.load_cases(str(self.cases))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], cid)
        self.assertEqual(loaded[0]["source"], "incident")
        self.assertEqual(loaded[0]["_incident"]["entry_id"], "run-demo#0001-abcdef01")

    def test_muc_so_khong_ton_tai_bao_loi(self):
        with self.assertRaises(evals.EvalFormatError):
            evals.add_incident(str(self.run_dir), "x", "khong-co", cases_dir=str(self.cases), no_kg=True)

    def test_ghi_kg_hop_le(self):
        # cases_dir phải nằm trong gốc dự án thì ref KG mới hợp lệ.
        repo_tmp = Path(tempfile.mkdtemp(dir=str(ROOT)))
        try:
            cases = repo_tmp / "cases"
            cid = evals.add_incident(str(self.run_dir), "Cai goi len host",
                                     "run-demo#0001-abcdef01", cases_dir=str(cases), no_kg=False)
            sys.path.insert(0, str(ROOT / "scripts"))
            import kg
            entities = kg.read_entities(str(self.run_dir))
            artifacts = [e for e in entities.values() if e.get("type") == "Artifact"]
            self.assertTrue(any(cid in (e.get("properties") or {}).get("path", "")
                                for e in artifacts), artifacts)
            errors, _warnings = kg.collect_validation(str(self.run_dir))[2:4]
            self.assertEqual(errors, [], errors)
        finally:
            shutil.rmtree(str(repo_tmp), ignore_errors=True)


# --------------------------------------------------------------------------- #
# TRIGGER --changed
# --------------------------------------------------------------------------- #
class ChangedTrigger(unittest.TestCase):
    def test_is_eval_relevant(self):
        self.assertTrue(evals.is_eval_relevant(["skills/ai-pipeline-evals/SKILL.md"]))
        self.assertTrue(evals.is_eval_relevant(["roles/module-dev.md"]))
        self.assertTrue(evals.is_eval_relevant(["templates/pre-commit.sample"]))
        self.assertTrue(evals.is_eval_relevant(["scripts/pipeline_guard.py"]))
        self.assertTrue(evals.is_eval_relevant(["AGENTS.md"]))
        self.assertFalse(evals.is_eval_relevant(["README.md", "scripts/evals.py", "tests/test_evals.py"]))

    def test_related_cases_theo_trigger(self):
        cases = evals.load_cases(str(REPO_CASES))
        related = evals.related_cases(cases, ["skills/ai-pipeline-sandbox/SKILL.md"])
        ids = {c["id"] for c in related}
        self.assertIn("host-install-ask", ids)
        self.assertNotIn("report-required-after-round", ids)

    @unittest.skipUnless(shutil.which("git"), "cần git")
    def test_changed_files_doc_git(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_repo(d)
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
            subprocess.run(["git", "init", "-q"], cwd=d, env=env, check=True)
            subprocess.run(["git", "add", "-A"], cwd=d, env=env, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=d, env=env, check=True)
            with open(os.path.join(d, "skills", "ai-pipeline", "SKILL.md"), "a",
                      encoding="utf-8") as f:
                f.write("\n# sua\n")
            changed = evals.changed_files(d)
            self.assertIn("skills/ai-pipeline/SKILL.md", changed)


if __name__ == "__main__":
    unittest.main()
