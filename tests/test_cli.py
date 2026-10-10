"""ai-pipeline CLI: init/update/uninstall must never alter or delete files the user already has."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cli(*args, cwd=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-m", "ai_pipeline", *args], capture_output=True, text=True,
                       encoding="utf-8", cwd=cwd or ROOT, env=env)
    return r.returncode, r.stdout + r.stderr


def snapshot(base):
    return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(base).rglob("*") if p.is_file() and ".git" not in p.parts}


class ExistingProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        files = {
            "AGENTS.md": "# Luat cua toi\nKhong doi\n",
            "CLAUDE.md": "# Claude rieng\nPhong cach X\n",
            ".gitignore": "node_modules/\n",
            ".claude/skills/my-skill/SKILL.md": "---\nname: my-skill\n---\nmine\n",
            ".claude/skills/ai-pipeline/SKILL.md": "---\nname: ai-pipeline\n---\nBAN CUA USER trung ten\n",
            ".agents/skills/other/SKILL.md": "other\n",
            "skills/own/SKILL.md": "own\n",
            "scripts/kg.py": "# script cua user trung ten\n",
            "scripts/mine.py": "print(1)\n",
        }
        for rel, text in files.items():
            p = self.t / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        self.before = snapshot(self.t)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_user_files_intact(self):
        now = snapshot(self.t)
        for rel, h in self.before.items():
            self.assertIn(rel, now, f"bi xoa: {rel}")
            self.assertEqual(now[rel], h, f"bi sua: {rel}")

    def test_init_never_touches_existing(self):
        rc, out = cli("init", str(self.t))
        self.assertEqual(rc, 0, out)
        self.assert_user_files_intact()
        self.assertTrue((self.t / ".ai-pipeline" / "AGENTS.md").is_file())
        self.assertTrue((self.t / ".claude/skills/ai-pipeline-intake/SKILL.md").is_file())
        self.assertIn("skip-dir", out)  # same-name user skill left whole
        self.assertFalse((self.t / ".claude/skills/ai-pipeline/references").exists())

    def test_init_idempotent_and_force_keeps_backup(self):
        cli("init", str(self.t))
        after1 = snapshot(self.t)
        cli("init", str(self.t))
        self.assertEqual(after1, snapshot(self.t))
        rc, out = cli("init", str(self.t), "--force")
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.t / "scripts/kg.py.bak").is_file())  # --force is opt-in and backs up
        self.assertEqual((self.t / "scripts/kg.py.bak").read_text(encoding="utf-8"), "# script cua user trung ten\n")

    def test_link_is_opt_in_and_reversible(self):
        cli("init", str(self.t), "--link")
        txt = (self.t / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("@.ai-pipeline/AGENTS.md", txt)
        self.assertTrue(txt.startswith("# Luat cua toi\nKhong doi\n"))
        rc, _ = cli("uninstall", str(self.t))
        self.assertEqual(rc, 0)
        self.assertEqual((self.t / "AGENTS.md").read_text(encoding="utf-8"), "# Luat cua toi\nKhong doi\n")
        self.assertFalse((self.t / ".ai-pipeline.json").exists())

    def test_uninstall_removes_only_ours(self):
        cli("init", str(self.t))
        (self.t / "roles/critic.md").write_text("user edited\n", encoding="utf-8")
        rc, out = cli("uninstall", str(self.t))
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.t / "roles/critic.md").is_file())  # edited -> kept
        self.assertFalse((self.t / "skills/ai-pipeline-intake").exists())
        self.assert_user_files_intact()

    def test_update_keeps_user_edits(self):
        cli("init", str(self.t))
        crit = self.t / "roles/critic.md"
        crit.write_text("user edited\n", encoding="utf-8")
        rc, out = cli("update", "--offline", str(self.t))
        self.assertEqual(rc, 0, out)
        self.assertEqual(crit.read_text(encoding="utf-8"), "user edited\n")
        self.assertIn("conflict", out)
        self.assertTrue((self.t / "roles/critic.md.new").is_file())
        self.assert_user_files_intact()

    def test_fresh_project_creates_instruction_files(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = cli("init", d)
            self.assertEqual(rc, 0, out)
            self.assertEqual((Path(d) / "AGENTS.md").read_text(encoding="utf-8"), "@.ai-pipeline/AGENTS.md\n")
            self.assertEqual((Path(d) / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n")
            self.assertEqual(json.loads((Path(d) / ".ai-pipeline.json").read_text(encoding="utf-8"))["agent"], "both")
            self.assertEqual(cli("doctor", d)[0], 0)


if __name__ == "__main__":
    unittest.main()


class Packs(unittest.TestCase):
    def test_install_refused_without_yes_and_status(self):
        with tempfile.TemporaryDirectory() as d:
            cli("init", d)
            ign = Path(d) / ".graphifyignore"
            ign.write_text("mine\n", encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "ai_pipeline", "pack", "install", "graphify", "--path", d],
                               capture_output=True, text=True, encoding="utf-8", cwd=ROOT, stdin=subprocess.DEVNULL,
                               env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8"))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertFalse((Path(d) / ".venv-graphify").exists())  # nothing installed without consent
            self.assertEqual(ign.read_text(encoding="utf-8"), "mine\n")
            rc, out = cli("pack", "status", "--path", d)
            self.assertEqual(rc, 0)
            self.assertIn("chua cai", out)
            self.assertTrue((ROOT / "templates" / "graphifyignore.template").is_file())
