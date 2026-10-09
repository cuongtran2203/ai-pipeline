"""ai-pipeline update: lay ban moi nhat tu git (repo cuc bo, khong mang)."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ai_pipeline import updater  # noqa: E402


def cli(*args, env_extra=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    env.pop(updater.REPO_ENV, None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, "-m", "ai_pipeline", *args], capture_output=True, text=True,
                       encoding="utf-8", cwd=ROOT, env=env)
    return r.returncode, r.stdout + r.stderr


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, check=True, capture_output=True)


def make_repo(root, tags):
    """tags: {tag: version}; moi tag co roles/critic.md = 'critic <tag>'."""
    root.mkdir()
    git(root, "init", "-q")
    for tag, ver in tags.items():
        (root / "skills" / "demo").mkdir(parents=True, exist_ok=True)
        (root / "skills" / "demo" / "SKILL.md").write_text(f"demo {tag}\n", encoding="utf-8")
        (root / "roles").mkdir(exist_ok=True)
        (root / "roles" / "critic.md").write_text(f"critic {tag}\n", encoding="utf-8")
        (root / "AGENTS.md").write_text(f"rules {tag}\n", encoding="utf-8")
        (root / "ai_pipeline").mkdir(exist_ok=True)
        (root / "ai_pipeline" / "__init__.py").write_text(f'__version__ = "{ver}"\n', encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", tag)
        git(root, "tag", tag)
    return str(root)


def tree(base):
    return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(base).rglob("*") if p.is_file()}


class VersionLogic(unittest.TestCase):
    def test_pick_latest_stable_skips_prerelease(self):
        tags = ["1.0.0", "1.1.0", "1.2.0.rc", "v1.10.0", "junk", "1.9.0-rc1"]
        self.assertEqual(updater.pick_latest(tags), "v1.10.0")
        self.assertEqual(updater.pick_latest(["1.0.0", "1.2.0.rc"]), "1.0.0")
        self.assertEqual(updater.pick_latest(["1.0.0", "1.2.0.rc"], pre=True), "1.2.0.rc")

    def test_is_newer(self):
        self.assertTrue(updater.is_newer("1.0.0", "1.0.0.rc"))
        self.assertFalse(updater.is_newer("1.0.0.rc", "1.0.0"))
        self.assertFalse(updater.is_newer("1.0.0", "1.0.0"))
        self.assertTrue(updater.is_newer("1.10.0", "1.9.0"))
        self.assertFalse(updater.is_newer("junk", "1.0.0"))


class UpdateFromGit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = make_repo(base / "repo", {"1.0.0": "1.0.0", "1.1.0": "1.1.0", "1.2.0.rc": "1.2.0.rc"})
        self.proj = base / "proj"
        self.proj.mkdir()
        rc, out = cli("init", str(self.proj), "--agent", "claude")
        self.assertEqual(rc, 0, out)

    def tearDown(self):
        self.tmp.cleanup()

    def installed(self, rel="roles/critic.md"):
        return (self.proj / rel).read_text(encoding="utf-8").strip()

    def test_default_picks_latest_stable(self):
        rc, out = cli("update", str(self.proj), "--repo", self.repo)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.installed("skills/demo/SKILL.md"), "demo 1.1.0")
        manifest = json.loads((self.proj / ".ai-pipeline.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "1.1.0")

    def test_pre_picks_rc(self):
        rc, out = cli("update", str(self.proj), "--repo", self.repo, "--pre")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.installed("skills/demo/SKILL.md"), "demo 1.2.0.rc")

    def test_ref_forces_tag(self):
        rc, out = cli("update", str(self.proj), "--repo", self.repo, "--ref", "1.0.0")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.installed("skills/demo/SKILL.md"), "demo 1.0.0")

    def test_env_repo(self):
        rc, out = cli("update", str(self.proj), env_extra={updater.REPO_ENV: self.repo})
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.installed("skills/demo/SKILL.md"), "demo 1.1.0")

    def test_check_writes_nothing(self):
        before = tree(self.proj)
        rc, out = cli("update", str(self.proj), "--repo", self.repo, "--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("1.1.0", out)
        self.assertIn("1.0.0.rc", out)
        self.assertEqual(tree(self.proj), before)

    def test_already_latest_is_noop(self):
        old = make_repo(Path(self.tmp.name) / "old", {"0.9.0": "0.9.0"})
        before = tree(self.proj)
        rc, out = cli("update", str(self.proj), "--repo", old)
        self.assertEqual(rc, 0, out)
        self.assertIn("da la ban moi nhat", out)
        self.assertEqual(tree(self.proj), before)

    def test_user_edit_kept_with_new_file(self):
        rc, out = cli("update", str(self.proj), "--repo", self.repo, "--ref", "1.0.0")
        self.assertEqual(rc, 0, out)
        crit = self.proj / "roles" / "critic.md"
        crit.write_text("user edited\n", encoding="utf-8")
        rc, out = cli("update", str(self.proj), "--repo", self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("conflict", out)
        self.assertEqual(crit.read_text(encoding="utf-8"), "user edited\n")
        self.assertEqual((self.proj / "roles" / "critic.md.new").read_text(encoding="utf-8").strip(), "critic 1.1.0")

    def test_bad_ref_fails_clean(self):
        before = tree(self.proj)
        tmpdir = Path(tempfile.gettempdir())
        stale = {p.name for p in tmpdir.glob("ai-pipeline-update-*")}
        rc, out = cli("update", str(self.proj), "--repo", self.repo, "--ref", "khong-co")
        self.assertNotEqual(rc, 0)
        self.assertIn("ai-pipeline:", out)
        self.assertEqual(tree(self.proj), before)
        self.assertEqual({p.name for p in tmpdir.glob("ai-pipeline-update-*")}, stale)

    def test_bad_repo_fails_clean(self):
        before = tree(self.proj)
        rc, out = cli("update", str(self.proj), "--repo", str(Path(self.tmp.name) / "khong-ton-tai"))
        self.assertNotEqual(rc, 0)
        self.assertIn("ai-pipeline:", out)
        self.assertEqual(tree(self.proj), before)

    def test_repo_without_framework_rejected(self):
        junk = Path(self.tmp.name) / "junk"
        junk.mkdir()
        git(junk, "init", "-q")
        (junk / "a.txt").write_text("x", encoding="utf-8")
        git(junk, "add", "-A")
        git(junk, "commit", "-q", "-m", "x")
        git(junk, "tag", "2.0.0")
        before = tree(self.proj)
        rc, out = cli("update", str(self.proj), "--repo", str(junk))
        self.assertNotEqual(rc, 0)
        self.assertIn("khong phai repo ai-pipeline", out)
        self.assertEqual(tree(self.proj), before)

    def test_offline_uses_local_payload(self):
        crit = self.proj / "roles" / "critic.md"
        crit.write_text("user edited\n", encoding="utf-8")
        rc, out = cli("update", "--offline", str(self.proj), "--repo", str(Path(self.tmp.name) / "khong-ton-tai"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(crit.read_text(encoding="utf-8"), "user edited\n")
        self.assertNotIn("demo", json.dumps(tree(self.proj)))

    def test_dry_run_writes_nothing(self):
        before = tree(self.proj)
        rc, out = cli("update", str(self.proj), "--repo", self.repo, "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(tree(self.proj), before)


if __name__ == "__main__":
    unittest.main()
