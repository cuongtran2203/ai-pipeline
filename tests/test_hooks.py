#!/usr/bin/env python3
"""Unittest stdlib cho wave HK: hook cuong che pipeline_guard + ai-pipeline hooks.

Phu: moi rule co ca chan + cho phep (ke ca `docker exec ... pip install`
duoc phep); duong dan Windows va POSIX; policy hong fail-closed cho nhan;
role gia mao qua tham so bi bo qua; hooks install merge giu hook nguoi
dung + idempotent + uninstall sach + JSON hong khong bi ghi de.
Chi viet vao thu muc tam; khong cham runs that. Chay:
    python -m unittest tests.test_hooks -v
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pipeline_guard as pg  # noqa: E402
from ai_pipeline import hooks as hooks_mod  # noqa: E402

ENV_KEYS = ("AI_PIPELINE_ROLE", "AI_PIPELINE_TASK_CONTEXT", "AI_PIPELINE_OWNS",
            "AI_PIPELINE_BUGFIX", "AI_PIPELINE_RUN_DIR", "AI_PIPELINE_WORKTREE",
            "AI_PIPELINE_BRANCH", "AI_PIPELINE_GUARD_POLICY", "AI_PIPELINE_GUARD_AUDIT")


def fresh_policy(**kw):
    p = copy.deepcopy(pg.DEFAULT_POLICY)
    for k, v in kw.items():
        if k == "rules":
            p["rules"].update(v)
        else:
            p[k] = v
    return p


def ev(tool, file_path=None, command=None, **extra_input):
    ti = dict(extra_input)
    if file_path is not None:
        ti["file_path"] = file_path
    if command is not None:
        ti["command"] = command
    return {"tool_name": tool, "tool_input": ti}


class EnvScrub(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def ctx(self, **kw):
        base = {"cwd": self.cwd, "run_dir": None, "role": "", "role_src": "",
                "owns": None, "bugfix": False, "branch": "feature-x"}
        base.update(kw)
        return base

    def check(self, event, policy=None, **ctxkw):
        return pg.evaluate(event, policy or fresh_policy(), self.ctx(**ctxkw))


class TestLabelProtection(EnvScrub):
    def test_deny_read_seal_audit(self):
        r = self.check(ev("Read", file_path="runs/t1/seal_audit.jsonl"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_deny_windows_path(self):
        r = self.check(ev("Edit", file_path="runs\\t1\\recipe_lock.json"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_deny_sealed_suffix(self):
        r = self.check(ev("Write", file_path="eval/labels.sealed.json"))
        self.assertFalse(r["allowed"])

    def test_deny_bash_touching_labels(self):
        r = self.check(ev("Bash", command="cat runs/t1/eval_manifest.json"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_allow_integrator(self):
        r = self.check(ev("Read", file_path="runs/t1/seal_audit.jsonl"), role="integrator")
        self.assertTrue(r["allowed"])

    def test_allow_evaluator(self):
        r = self.check(ev("Bash", command="cat runs/t1/seal_audit.jsonl"), role="evaluator")
        self.assertTrue(r["allowed"])

    def test_allow_normal_file(self):
        r = self.check(ev("Read", file_path="src/model.py"))
        self.assertTrue(r["allowed"])

    def test_fake_role_param_ignored(self):
        e = ev("Read", file_path="seal_audit.jsonl", role="integrator")
        r = self.check(e)
        self.assertFalse(r["allowed"], "role trong event phai bi bo qua")

    def test_rule_off_allows(self):
        p = fresh_policy(rules={"label_protection": False})
        r = self.check(ev("Read", file_path="seal_audit.jsonl"), policy=p)
        self.assertTrue(r["allowed"])


class TestHostInstall(EnvScrub):
    HOST_CMDS = ["pip install requests", "pip3 install -r req.txt",
                 "python -m pip install numpy", "npm install foo", "npm i foo",
                 "yarn add foo", "sudo apt-get install -y libx",
                 "conda install pytorch", "brew install wget",
                 "cargo install ripgrep", "go install ./..."]

    def test_deny_all_host_installers(self):
        for c in self.HOST_CMDS:
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertFalse(r["allowed"], c)
                self.assertEqual(r["rule"], "host_install")

    def test_allow_in_docker_exec(self):
        r = self.check(ev("Bash", command="docker exec cnt pip install requests"))
        self.assertTrue(r["allowed"])

    def test_allow_in_docker_run(self):
        r = self.check(ev("Bash", command="docker run --rm img pip install torch"))
        self.assertTrue(r["allowed"])

    def test_allow_in_ssh_docker(self):
        r = self.check(ev("Bash", command="ssh gpu01 docker exec c pip install torch"))
        self.assertTrue(r["allowed"])

    def test_allow_listed_package(self):
        p = fresh_policy(allowed_packages=["torch"])
        r = self.check(ev("Bash", command="pip install torch==2.0"), policy=p)
        self.assertTrue(r["allowed"])

    def test_listed_package_does_not_whitelist_others(self):
        p = fresh_policy(allowed_packages=["torch"])
        r = self.check(ev("Bash", command="pip install requests"), policy=p)
        self.assertFalse(r["allowed"])

    def test_allow_plain_command(self):
        r = self.check(ev("Bash", command="python scripts/validate_spec.py spec.md"))
        self.assertTrue(r["allowed"])


class TestDangerousDocker(EnvScrub):
    def test_deny_privileged(self):
        r = self.check(ev("Bash", command="docker run --privileged img"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "dangerous_docker")

    def test_deny_net_host_variants(self):
        for c in ("docker run --net=host img", "docker run --net host img",
                  "docker run --network host img", "docker run --network=host img"):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertFalse(r["allowed"], c)

    def test_deny_system_prune(self):
        r = self.check(ev("Bash", command="docker system prune -f"))
        self.assertFalse(r["allowed"])

    def test_deny_rm_foreign_container(self):
        r = self.check(ev("Bash", command="docker rm -f mydb"))
        self.assertFalse(r["allowed"])

    def test_allow_rm_own_container(self):
        r = self.check(ev("Bash", command="docker rm -f aipipeline-run1-worker"))
        self.assertTrue(r["allowed"])

    def test_allow_plain_docker(self):
        r = self.check(ev("Bash", command="docker exec aipipeline-run1-w python train.py"))
        self.assertTrue(r["allowed"])


class TestOwnership(EnvScrub):
    def test_deny_outside_owns(self):
        own = os.path.join(self.cwd, "modules", "m1")
        os.makedirs(own)
        r = self.check(ev("Write", file_path=os.path.join(self.cwd, "other", "x.py")),
                       owns=[own])
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "ownership")

    def test_allow_inside_owns(self):
        own = os.path.join(self.cwd, "modules", "m1")
        os.makedirs(own)
        r = self.check(ev("Edit", file_path=os.path.join(own, "train.py")), owns=[own])
        self.assertTrue(r["allowed"])

    def test_read_outside_is_allowed(self):
        r = self.check(ev("Read", file_path=os.path.join(self.cwd, "other", "x.py")),
                       owns=[os.path.join(self.cwd, "modules", "m1")])
        self.assertTrue(r["allowed"])

    def test_missing_ownership_fail_open(self):
        r = self.check(ev("Write", file_path=os.path.join(self.cwd, "any", "x.py")))
        self.assertTrue(r["allowed"])


class TestBugfix(EnvScrub):
    def test_deny_edit_tests(self):
        r = self.check(ev("Edit", file_path="tests/test_model.py"), bugfix=True)
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "bugfix_tests")

    def test_deny_windows_tests(self):
        r = self.check(ev("Write", file_path="pkg\\tests\\foo_test.py"), bugfix=True)
        self.assertFalse(r["allowed"])

    def test_allow_source_edit(self):
        r = self.check(ev("Edit", file_path="src/model.py"), bugfix=True)
        self.assertTrue(r["allowed"])

    def test_no_flag_allows_tests(self):
        r = self.check(ev("Edit", file_path="tests/test_model.py"))
        self.assertTrue(r["allowed"])


class TestDestructive(EnvScrub):
    def test_deny_rm_rf_absolute(self):
        r = self.check(ev("Bash", command="rm -rf /etc/data"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "destructive")

    def test_deny_rm_rf_root(self):
        r = self.check(ev("Bash", command="rm -rf /"))
        self.assertFalse(r["allowed"])

    def test_deny_rm_rf_env_var(self):
        r = self.check(ev("Bash", command="rm -rf $OUTDIR"))
        self.assertFalse(r["allowed"])

    def test_allow_rm_rf_tmp(self):
        target = os.path.join(tempfile.gettempdir(), "hk-probe-x")
        r = self.check(ev("Bash", command=f"rm -rf {target}"))
        self.assertTrue(r["allowed"])

    def test_allow_rm_rf_inside_worktree(self):
        r = self.check(ev("Bash", command="rm -rf build/cache"))
        self.assertTrue(r["allowed"])

    def test_deny_push_force(self):
        for c in ("git push --force origin main", "git push -f origin main"):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertFalse(r["allowed"], c)

    def test_deny_reset_hard_on_main(self):
        r = self.check(ev("Bash", command="git reset --hard HEAD~1"), branch="main")
        self.assertFalse(r["allowed"])

    def test_deny_reset_hard_unknown_branch(self):
        r = self.check(ev("Bash", command="git reset --hard"), branch=None)
        self.assertFalse(r["allowed"])

    def test_allow_reset_hard_on_feature(self):
        r = self.check(ev("Bash", command="git reset --hard HEAD~1"), branch="feature-x")
        self.assertTrue(r["allowed"])

    def test_allow_plain_git(self):
        r = self.check(ev("Bash", command="git status --short"))
        self.assertTrue(r["allowed"])


class TestCorruptPolicy(EnvScrub):
    def _broken(self):
        p = os.path.join(self.cwd, ".ai-pipeline", "guard_policy.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("{hong json")
        return p

    def test_corrupt_fail_closed_for_labels(self):
        pol = self._broken()
        audit = os.path.join(self.cwd, "guard_audit.jsonl")
        res, ok, _ = pg.decide(ev("Read", file_path="seal_audit.jsonl"),
                               self.cwd, policy_path=pol, audit_path=audit)
        self.assertFalse(res["allowed"])
        self.assertEqual(res["rule"], "label_protection")
        self.assertTrue(ok)

    def test_corrupt_fail_closed_for_docker(self):
        pol = self._broken()
        res, _, _ = pg.decide(ev("Bash", command="docker run --privileged img"),
                              self.cwd, policy_path=pol,
                              audit_path=os.path.join(self.cwd, "a.jsonl"))
        self.assertFalse(res["allowed"])
        self.assertEqual(res["rule"], "dangerous_docker")

    def test_corrupt_fail_open_for_ownership(self):
        pol = self._broken()
        res, _, _ = pg.decide(ev("Write", file_path=os.path.join(self.cwd, "x.py")),
                              self.cwd, policy_path=pol,
                              audit_path=os.path.join(self.cwd, "a.jsonl"))
        self.assertTrue(res["allowed"])

    def test_audit_record_shape(self):
        audit = os.path.join(self.cwd, "guard_audit.jsonl")
        res, ok, _ = pg.decide(ev("Bash", command="pip install x"),
                               self.cwd, policy_path=None, audit_path=audit)
        self.assertFalse(res["allowed"])
        self.assertTrue(ok)
        with open(audit, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        for k in ("ts", "allowed", "rule", "reason"):
            self.assertIn(k, rec)


class TestRoleFromEnv(EnvScrub):
    def test_env_role_allows_label(self):
        os.environ["AI_PIPELINE_ROLE"] = "integrator"
        res, _, _ = pg.decide(ev("Read", file_path="seal_audit.jsonl"),
                              self.cwd, policy_path=None,
                              audit_path=os.path.join(self.cwd, "a.jsonl"))
        self.assertTrue(res["allowed"])

    def test_no_env_role_denies_label(self):
        res, _, _ = pg.decide(ev("Read", file_path="seal_audit.jsonl"),
                              self.cwd, policy_path=None,
                              audit_path=os.path.join(self.cwd, "a.jsonl"))
        self.assertFalse(res["allowed"])


class TestGuardSubprocess(EnvScrub):
    def _run(self, event, extra=()):
        audit = os.path.join(self.cwd, "guard_audit.jsonl")
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
        for k in ENV_KEYS:
            env.pop(k, None)
        cmd = [sys.executable, os.path.join(SCRIPTS, "pipeline_guard.py"),
               "--cwd", self.cwd, "--audit", audit, *extra]
        return subprocess.run(cmd, input=json.dumps(event), capture_output=True,
                              text=True, encoding="utf-8", env=env), audit

    def test_deny_exit2_with_stderr_and_json(self):
        r, audit = self._run(ev("Bash", command="pip install evil"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("CHAN", r.stderr)
        self.assertIn("host_install", r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_allow_exit0_silent(self):
        r, _ = self._run(ev("Bash", command="python scripts/validate_spec.py s.md"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_check_mode(self):
        r, _ = self._run(None, extra=("--check", "--tool", "Bash", "--input",
                                     '{"command": "docker run --privileged x"}'))
        # event None -> dung --input; _run truyen stdin "null" nhung --input thang
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)


class TestHooksInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = self.tmp.name
        self.sp = os.path.join(self.proj, ".claude", "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_settings(self, data):
        os.makedirs(os.path.dirname(self.sp), exist_ok=True)
        with open(self.sp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _read_settings(self):
        with open(self.sp, encoding="utf-8") as f:
            return json.load(f)

    def _sha(self):
        h = hashlib.sha256()
        with open(self.sp, "rb") as f:
            h.update(f.read())
        return h.hexdigest()

    def test_install_merges_and_keeps_user_hooks(self):
        self._write_settings({
            "permissions": {"allow": ["Bash(git *)"]},
            "hooks": {"PostToolUse": [{"matcher": "Edit",
                                       "hooks": [{"type": "command",
                                                  "command": "user-lint.sh"}]}],
                      "PreToolUse": [{"matcher": "Bash",
                                      "hooks": [{"type": "command",
                                                 "command": "user-check.sh"}]}]}})
        changed, _ = hooks_mod.install(self.proj)
        self.assertTrue(changed)
        data = self._read_settings()
        self.assertEqual(data["permissions"], {"allow": ["Bash(git *)"]})
        post = data["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(post, "user-lint.sh")
        pre_cmds = [h["command"] if isinstance(h.get("command"), str)
                    else h.get("command")
                    for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
        flat = json.dumps(data)
        self.assertIn("user-check.sh", flat)
        self.assertIn("pipeline_guard.py", flat)

    def test_install_idempotent(self):
        hooks_mod.install(self.proj)
        h1 = self._sha()
        changed, _ = hooks_mod.install(self.proj)
        self.assertFalse(changed)
        self.assertEqual(h1, self._sha())

    def test_uninstall_clean_keeps_user(self):
        self._write_settings({
            "hooks": {"PreToolUse": [{"matcher": "Bash",
                                      "hooks": [{"type": "command",
                                                 "command": "user-check.sh"}]}]}})
        hooks_mod.install(self.proj)
        changed, _ = hooks_mod.uninstall(self.proj)
        self.assertTrue(changed)
        data = self._read_settings()
        flat = json.dumps(data)
        self.assertIn("user-check.sh", flat)
        self.assertNotIn("pipeline_guard.py", flat)
        changed2, _ = hooks_mod.uninstall(self.proj)
        self.assertFalse(changed2)

    def test_broken_json_not_overwritten(self):
        os.makedirs(os.path.dirname(self.sp), exist_ok=True)
        with open(self.sp, "w", encoding="utf-8", newline="\n") as f:
            f.write("{hong")
        before = self._sha()
        with self.assertRaises(hooks_mod.HooksError):
            hooks_mod.install(self.proj)
        self.assertEqual(before, self._sha())

    def test_cli_hooks_roundtrip(self):
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
        for args in (["hooks", "install", "--path", self.proj],
                     ["hooks", "status", "--path", self.proj],
                     ["hooks", "install", "--path", self.proj],
                     ["hooks", "uninstall", "--path", self.proj]):
            r = subprocess.run([sys.executable, "-m", "ai_pipeline", *args],
                               capture_output=True, text=True, encoding="utf-8",
                               cwd=ROOT, env=env)
            self.assertEqual(r.returncode, 0, args + [r.stdout + r.stderr])
        self.assertNotIn("pipeline_guard.py", json.dumps(self._read_settings()))


if __name__ == "__main__":
    unittest.main()
