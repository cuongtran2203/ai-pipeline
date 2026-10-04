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

ENV_KEYS = ("AI_PIPELINE_ROLE", "AI_PIPELINE_TASK", "AI_PIPELINE_TASK_CONTEXT", "AI_PIPELINE_OWNS",
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
        # P3 FH2: policy hong -> Bash bi policy_config deny (fail-closed nhom
        # ghi), ke ca lenh docker nguy hiem (van deny, rule config di truoc).
        pol = self._broken()
        res, _, _ = pg.decide(ev("Bash", command="docker run --privileged img"),
                              self.cwd, policy_path=pol,
                              audit_path=os.path.join(self.cwd, "a.jsonl"))
        self.assertFalse(res["allowed"])
        self.assertEqual(res["rule"], "policy_config")

    def test_corrupt_denies_write_allows_read(self):
        # P3 FH2: policy hong deny nhom ghi (Edit/Write/Bash) kem thong diep
        # cau hinh ro rang, van cho Read thuong; audit van ghi.
        pol = self._broken()
        for event in (ev("Write", file_path=os.path.join(self.cwd, "x.py")),
                      ev("Edit", file_path=os.path.join(self.cwd, "x.py")),
                      ev("Bash", command="ls")):
            with self.subTest(event=event):
                res, ok, _ = pg.decide(
                    event, self.cwd, policy_path=pol,
                    audit_path=os.path.join(self.cwd, "a.jsonl"))
                self.assertFalse(res["allowed"])
                self.assertEqual(res["rule"], "policy_config")
                self.assertIn("policy", res["reason"].lower())
                self.assertTrue(ok)
        res, ok, audit = pg.decide(ev("Read", file_path="src/main.py"),
                                   self.cwd, policy_path=pol,
                                   audit_path=os.path.join(self.cwd, "b.jsonl"))
        self.assertTrue(res["allowed"], res)
        self.assertTrue(ok)
        with open(audit, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        self.assertTrue(rec["policy_corrupt"])

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
    def test_env_role_is_ignored(self):
        # P2 FH2 (RV5): AI_PIPELINE_ROLE tu khai BI BO; role chi tu context.
        os.environ["AI_PIPELINE_ROLE"] = "integrator"
        res, _, _ = pg.decide(ev("Read", file_path="seal_audit.jsonl"),
                              self.cwd, policy_path=None,
                              audit_path=os.path.join(self.cwd, "a.jsonl"))
        self.assertFalse(res["allowed"], "env role phai bi bo qua")

    def test_role_from_task_context(self):
        run = os.path.join(self.tmp.name, "run")
        os.makedirs(run)
        plan = {"run_id": "rr", "tasks": [
            {"id": "I1", "role": "integrator", "owns": [], "bugfix": False}]}
        with open(os.path.join(run, "plan.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(plan, f)
        pg.context_write(run)
        os.environ["AI_PIPELINE_TASK"] = "I1"
        res, _, _ = pg.decide(ev("Read", file_path="seal_audit.jsonl"),
                              self.cwd, policy_path=None, run_dir=run,
                              audit_path=os.path.join(self.cwd, "a.jsonl"))
        self.assertTrue(res["allowed"], res)

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


class TestRecursiveSearchFH(EnvScrub):
    """Wave FH-1: tim kiem de quy phu len file nhan bi chan (su co rg timesheet-ocr)."""

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.cwd, "labels"))
        with open(os.path.join(self.cwd, "labels", "gold.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write('{"y": 1}')
        os.makedirs(os.path.join(self.cwd, "src"))
        with open(os.path.join(self.cwd, "src", "main.py"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write("print(1)\n")
        os.makedirs(os.path.join(self.cwd, ".git"))
        self.pol = fresh_policy(protected_paths=["labels/gold.json"])

    def check(self, event, policy=None, **ctxkw):
        return pg.evaluate(event, policy or self.pol, self.ctx(**ctxkw))

    def test_deny_rg_dot(self):
        r = self.check(ev("Bash", command="rg -n . ."))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_deny_grep_r(self):
        for c in ("grep -rn password .", "grep -R foo .",
                  "egrep -rn 'a|b' .", "ag foo ."):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertFalse(r["allowed"], c)
                self.assertEqual(r["rule"], "label_protection")

    def test_deny_git_grep(self):
        r = self.check(ev("Bash", command="git grep -n password"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_deny_find_exec(self):
        r = self.check(ev("Bash", command="find . -type f -exec grep -l foo {} +"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_deny_find_piped(self):
        r = self.check(ev("Bash", command="find . -type f | xargs grep foo"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_deny_findstr(self):
        r = self.check(ev("Bash", command="findstr /s foo ."))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_deny_select_string(self):
        r = self.check(ev("Bash", command='Select-String -Recurse -Pattern "foo" -Path "."'))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_deny_gci_piped(self):
        r = self.check(ev("Bash", command="Get-ChildItem -Recurse . | Select-String foo"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_deny_explicit_labels_dir(self):
        r = self.check(ev("Bash", command="rg foo labels"))
        self.assertFalse(r["allowed"])

    def test_allow_subdir_without_labels(self):
        for c in ("rg foo src", "grep -rn foo src", "find src -exec cat {} +"):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertTrue(r["allowed"], c)

    def test_allow_excluded(self):
        r = self.check(ev("Bash", command="rg foo . --glob '!*.json'"))
        self.assertTrue(r["allowed"])
        r = self.check(ev("Bash", command="grep -rn foo . --exclude='*.json'"))
        self.assertTrue(r["allowed"])

    def test_exclude_unrelated_does_not_allow(self):
        r = self.check(ev("Bash", command="rg foo . --glob '!*.png'"))
        self.assertFalse(r["allowed"])

    def test_allow_integrator_recursive(self):
        r = self.check(ev("Bash", command="rg -n . ."), role="integrator")
        self.assertTrue(r["allowed"])

    def test_allow_plain_grep_single_file(self):
        r = self.check(ev("Bash", command="grep foo src/main.py"))
        self.assertTrue(r["allowed"])

    def test_echo_search_words_not_flagged(self):
        r = self.check(ev("Bash", command='echo "rg foo ."'))
        self.assertTrue(r["allowed"])

    def test_symlink_root_resolved_posix(self):
        link = os.path.join(self.cwd, "alias")
        try:
            os.symlink(os.path.join(self.cwd, "labels"), link)
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"khong tao duoc symlink: {e}")
        if not os.path.islink(link):
            self.skipTest("symlink khong ton tai sau khi tao")
        r = self.check(ev("Bash", command="rg foo alias"))
        if os.name == "posix":
            self.assertFalse(r["allowed"])
        else:
            # Windows: symlink co the can quyen; chi khang dinh khong crash
            self.assertIn(r["rule"], ("label_protection", None))


class TestPushForceFH(EnvScrub):
    """Wave FH-2: refspec '+' va --mirror bi chan nhu --force."""

    def test_deny_plus_refspec(self):
        for c in ("git push origin +master", "git push origin +HEAD:master",
                  "git push origin +refs/heads/a:refs/heads/b"):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertFalse(r["allowed"], c)
                self.assertEqual(r["rule"], "destructive")

    def test_deny_mirror(self):
        for c in ("git push --mirror", "git push --mirror origin"):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertFalse(r["allowed"], c)

    def test_deny_force_with_git_flags(self):
        r = self.check(ev("Bash", command="git -C sub push --force origin main"))
        self.assertFalse(r["allowed"])
        r = self.check(ev("Bash", command="sudo git push --force origin main"))
        self.assertFalse(r["allowed"])

    def test_allow_plain_push(self):
        for c in ("git push origin master", "git push -u origin feat",
                  "git push", "git stash push -m save",
                  'git commit -m "reset --hard oops"'):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertTrue(r["allowed"], c)


class TestHostInstallFH(EnvScrub):
    """Wave FH-3 (mo rong R2) + FH-5 (bo FP quote)."""

    DENY = ["pipx install foo", "pipx inject foo", "easy_install foo",
            "pip download foo", "python -m pip download foo",
            "uv add foo", "uv sync", "poetry add foo", "poetry install",
            "pdm add foo", "pdm install",
            'bash -c "pip install x"', "sudo pip install x",
            "env FOO=1 pip install x", "python -m pip install x"]

    def test_deny_new_installers(self):
        for c in self.DENY:
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertFalse(r["allowed"], c)
                self.assertEqual(r["rule"], "host_install")

    def test_allow_in_container(self):
        for c in ("docker exec cnt pipx install foo",
                  "docker run --rm img uv sync",
                  "ssh gpu01 docker exec c poetry install"):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertTrue(r["allowed"], c)

    def test_allow_quoted_false_positives(self):
        for c in ("echo 'pip install is bad'",
                  'git commit -m "do pip install x"',
                  "grep 'pip install' req.txt",
                  'python -c "print(1)"',
                  "sh setup.sh"):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertTrue(r["allowed"], c)

    def test_allow_heredoc(self):
        cmd = "cat <<EOF\npip install x\nEOF"
        r = self.check(ev("Bash", command=cmd))
        self.assertTrue(r["allowed"])

    def test_chained_install_still_caught(self):
        r = self.check(ev("Bash", command="echo hi && pip install x"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "host_install")


class TestRmTmpFH(EnvScrub):
    """Wave FH-4: cho phep xoa trong tmp he thong; van chan nguy hiem."""

    def test_allow_system_tmp(self):
        for target in ("/tmp/x_task_dir",
                       os.path.join(tempfile.gettempdir(), "x_task_dir")):
            with self.subTest(target=target):
                r = self.check(ev("Bash", command=f"rm -rf {target}"))
                self.assertTrue(r["allowed"], target)

    def test_allow_quoted_relative(self):
        r = self.check(ev("Bash", command='rm -rf "build/cache"'))
        self.assertTrue(r["allowed"])

    def test_allow_absolute_inside_worktree(self):
        target = os.path.join(self.cwd, "build", "cache")
        r = self.check(ev("Bash", command=f"rm -rf {target}"))
        self.assertTrue(r["allowed"])

    def test_deny_dangerous(self):
        parent = os.path.dirname(self.cwd.rstrip(os.sep))
        cases = ["rm -rf /", "rm -rf ~", "rm -rf .", "rm -rf ..",
                 "rm -rf *", "rm -rf ..", "rm -rf build/../..",
                 "rm -rf /tmp", "rm -rf $OUTDIR",
                 f"rm -rf {parent}",
                 f"rm -rf {os.path.abspath(os.sep)}"]
        for c in cases:
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertFalse(r["allowed"], c)
                self.assertEqual(r["rule"], "destructive")


class TestContextWriteFH(EnvScrub):
    """Wave FH-6: context write cap role/owns tu plan.json."""

    def _run(self, run_id="demo"):
        run = os.path.join(self.tmp.name, "run")
        os.makedirs(run)
        plan = {"run_id": run_id, "title": "t", "tasks": [
            {"id": "T1", "title": "m1", "role": "module-dev",
             "owns": ["modules/m1"], "bugfix": False,
             "change": "x", "acceptance": "y"},
            {"id": "T2", "title": "fix", "role": "module-dev",
             "owns": ["modules/m1"], "bugfix": True,
             "change": "x", "acceptance": "y"},
        ]}
        with open(os.path.join(run, "plan.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(plan, f, ensure_ascii=False)
        return run

    def test_write_and_resolve_by_env(self):
        run = self._run()
        n, path = pg.context_write(run)
        self.assertEqual(n, 2)
        doc = json.load(open(path, encoding="utf-8"))
        self.assertEqual(doc["tasks"]["T1"]["role"], "module-dev")
        self.assertEqual(doc["tasks"]["T2"]["bugfix"], True)
        os.environ["AI_PIPELINE_TASK"] = "T1"
        ctx = pg.build_context(self.cwd, run)
        self.assertEqual(ctx["role"], "module-dev")
        self.assertEqual(ctx["owns"], ["modules/m1"])
        self.assertFalse(ctx["bugfix"])
        os.environ["AI_PIPELINE_TASK"] = "T2"
        ctx = pg.build_context(self.cwd, run)
        self.assertTrue(ctx["bugfix"])

    def test_resolve_by_worktree_dirname(self):
        run = self._run(run_id="demo")
        pg.context_write(run)
        wt = os.path.join(self.tmp.name, "demo-t1")
        os.makedirs(wt, exist_ok=True)
        ctx = pg.build_context(wt, run)
        self.assertEqual(ctx["role"], "module-dev")

    def test_unknown_task_fail_closed(self):
        run = self._run()
        pg.context_write(run)
        os.environ["AI_PIPELINE_TASK"] = "NOPE"
        ctx = pg.build_context(self.cwd, run)
        self.assertEqual(ctx["role"], "")
        r = pg.evaluate(ev("Read", file_path="seal_audit.jsonl"),
                        fresh_policy(), ctx)
        self.assertFalse(r["allowed"])

    def test_legacy_context_shape(self):
        run = self._run()
        with open(os.path.join(run, "task_context.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump({"role": "integrator"}, f)
        ctx = pg.build_context(self.cwd, run)
        self.assertEqual(ctx["role"], "integrator")

    def test_no_event_role_spoof(self):
        run = self._run()
        pg.context_write(run)
        os.environ["AI_PIPELINE_TASK"] = "T1"
        ctx = pg.build_context(self.cwd, run)
        e = ev("Read", file_path="seal_audit.jsonl", role="integrator")
        r = pg.evaluate(e, fresh_policy(), ctx)
        self.assertFalse(r["allowed"])

    def test_plan_in_artifacts_fallback(self):
        run = os.path.join(self.tmp.name, "run2")
        adir = os.path.join(run, "artifacts", "A")
        os.makedirs(adir)
        with open(os.path.join(adir, "plan.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump({"run_id": "r2", "tasks": [
                {"id": "T9", "role": "integrator"}]}, f)
        n, _ = pg.context_write(run)
        self.assertEqual(n, 1)

    def test_cli_context_write(self):
        run = self._run()
        rc = pg.main(["context", "write", run])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(run, "task_context.json")))
        rc = pg.main(["context", "write", os.path.join(self.tmp.name, "nope")])
        self.assertEqual(rc, 1)


class TestHooksOwnGroupFH2(unittest.TestCase):
    """FH2-1: install luon tao NHOM RIENG matcher du, khong dung nhom user."""

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

    def _user_settings(self):
        return {
            "permissions": {"allow": ["Bash(git *)"]},
            "hooks": {"PreToolUse": [
                {"matcher": "Read",
                 "hooks": [{"type": "command", "command": "user-read.sh"}]},
                {"matcher": "Bash",
                 "hooks": [{"type": "command", "command": "user-bash.sh"}]},
                {"matcher": "",
                 "hooks": [{"type": "command", "command": "user-any.sh"}]},
            ]}}

    def test_full_matcher_value(self):
        self.assertEqual(
            hooks_mod.MATCHER,
            "Read|Edit|MultiEdit|Write|NotebookEdit|Bash|Grep|Glob")

    def test_template_matcher_matches(self):
        with open(os.path.join(ROOT, "templates", "hooks.settings.template.json"),
                  encoding="utf-8") as f:
            tpl = json.load(f)
        self.assertEqual(tpl["hooks"]["PreToolUse"][0]["matcher"],
                         hooks_mod.MATCHER)

    def test_install_creates_own_group_keeps_user(self):
        before = self._user_settings()
        self._write_settings(copy.deepcopy(before))
        changed, _ = hooks_mod.install(self.proj)
        self.assertTrue(changed)
        data = self._read_settings()
        groups = data["hooks"]["PreToolUse"]
        self.assertEqual(len(groups), 4)  # 3 user + 1 rieng
        for g, want in zip(groups[:3], before["hooks"]["PreToolUse"]):
            self.assertEqual(g["matcher"], want["matcher"])
            self.assertEqual(g["hooks"], want["hooks"])
        own = groups[3]
        self.assertEqual(own["matcher"], hooks_mod.MATCHER)
        self.assertEqual(len(own["hooks"]), 1)
        self.assertIn("pipeline_guard.py", json.dumps(own))
        self.assertEqual(data["permissions"], {"allow": ["Bash(git *)"]})
        self.assertEqual(hooks_mod.find_ours(data), 1)

    def test_install_idempotent_own_group(self):
        self._write_settings(self._user_settings())
        hooks_mod.install(self.proj)
        with open(self.sp, "rb") as f:
            h1 = hashlib.sha256(f.read()).hexdigest()
        changed, _ = hooks_mod.install(self.proj)
        self.assertFalse(changed)
        with open(self.sp, "rb") as f:
            self.assertEqual(h1, hashlib.sha256(f.read()).hexdigest())

    def test_uninstall_removes_only_own_group(self):
        self._write_settings(self._user_settings())
        hooks_mod.install(self.proj)
        changed, _ = hooks_mod.uninstall(self.proj)
        self.assertTrue(changed)
        data = self._read_settings()
        self.assertEqual(data["hooks"]["PreToolUse"],
                         self._user_settings()["hooks"]["PreToolUse"])
        self.assertNotIn("pipeline_guard.py", json.dumps(data))

    def test_uninstall_cleans_legacy_merged_group(self):
        # Ban cu tung chen guard vao nhom user: uninstall van don sach,
        # giu hook + matcher cua user.
        self._write_settings({
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "user-check.sh"},
                {"type": "command", "command": "python",
                 "args": ["${CLAUDE_PROJECT_DIR}/scripts/pipeline_guard.py"]},
            ]}]}})
        changed, _ = hooks_mod.uninstall(self.proj)
        self.assertTrue(changed)
        data = self._read_settings()
        groups = data["hooks"]["PreToolUse"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "Bash")
        self.assertEqual(groups[0]["hooks"],
                         [{"type": "command", "command": "user-check.sh"}])


class TestAllowedPackagesArgvFH2(EnvScrub):
    """FH2-2: allowed_packages so khop CHINH XAC theo argv."""

    def check(self, event, policy=None, **ctxkw):
        return pg.evaluate(event, policy or fresh_policy(), self.ctx(**ctxkw))

    def test_substring_does_not_allow(self):
        p = fresh_policy(allowed_packages=["safe"])
        r = self.check(ev("Bash", command="pip install unsafe evil"), policy=p)
        self.assertFalse(r["allowed"], "substring 'safe' trong 'unsafe' phai deny")
        r = self.check(ev("Bash", command="pip install safe"), policy=p)
        self.assertTrue(r["allowed"])

    def test_normalization(self):
        p = fresh_policy(allowed_packages=["Safe_Lib"])
        for c in ("pip install safe-lib", "pip install SAFE_LIB==2.0",
                  "pip install safe_lib[extra]>=1"):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c), policy=p)
                self.assertTrue(r["allowed"], c)
        r = self.check(ev("Bash", command="pip install safe-libx"), policy=p)
        self.assertFalse(r["allowed"])

    def test_all_packages_must_be_allowed(self):
        p = fresh_policy(allowed_packages=["torch"])
        r = self.check(ev("Bash", command="pip install torch requests"), policy=p)
        self.assertFalse(r["allowed"])
        r = self.check(ev("Bash", command="pip install torch && pip install requests"),
                       policy=p)
        self.assertFalse(r["allowed"])

    def test_requirement_file_and_url_deny(self):
        p = fresh_policy(allowed_packages=["torch"])
        for c in ("pip install -r req.txt",
                  "pip install torch -r req.txt",
                  "pip install https://x/torch-2.0.tar.gz",
                  "pip install git+https://x/torch.git",
                  "pip install -e .",
                  "pip install torch @ https://x/torch.whl"):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c), policy=p)
                self.assertFalse(r["allowed"], c)

    def test_value_flags_skipped(self):
        p = fresh_policy(allowed_packages=["torch"])
        for c in ("pip install --index-url https://x torch",
                  "pip install -U torch",
                  "pip install --no-cache-dir torch==2.0"):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c), policy=p)
                self.assertTrue(r["allowed"], c)

    def test_npm_and_uv_pip(self):
        p = fresh_policy(allowed_packages=["left-pad", "torch"])
        r = self.check(ev("Bash", command="npm install left-pad@1.3.0"), policy=p)
        self.assertTrue(r["allowed"])
        r = self.check(ev("Bash", command="npm i left-pad evil"), policy=p)
        self.assertFalse(r["allowed"])
        r = self.check(ev("Bash", command="uv pip install torch"), policy=p)
        self.assertTrue(r["allowed"])
        r = self.check(ev("Bash", command="uv pip install requests"), policy=p)
        self.assertFalse(r["allowed"])

    def test_wrapper_with_allowed_pkg(self):
        p = fresh_policy(allowed_packages=["torch"])
        r = self.check(ev("Bash", command='bash -c "pip install torch"'), policy=p)
        self.assertTrue(r["allowed"])
        r = self.check(ev("Bash", command="sudo pip install torch"), policy=p)
        self.assertTrue(r["allowed"])

    def test_no_allowed_list_denies_all(self):
        r = self.check(ev("Bash", command="pip install torch"))
        self.assertFalse(r["allowed"])


class TestWorktreeResolveFH2(EnvScrub):
    """FH2-3: chon task theo ten worktree <run_id>-<task lowercase>."""

    def _run(self, run_id, tasks):
        run = os.path.join(self.tmp.name, "run-" + run_id)
        os.makedirs(run, exist_ok=True)
        plan = {"run_id": run_id, "tasks": tasks}
        with open(os.path.join(run, "plan.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(plan, f, ensure_ascii=False)
        pg.context_write(run)
        return run

    def _task(self, tid, role="integrator"):
        return {"id": tid, "role": role, "owns": [], "bugfix": False}

    def test_probe_rr_i1_lower_and_upper(self):
        run = self._run("rr", [self._task("I1")])
        for base in ("rr-i1", "rr-I1"):
            with self.subTest(cwd=base):
                wt = os.path.join(self.tmp.name, base)
                os.makedirs(wt, exist_ok=True)
                ctx = pg.build_context(wt, run)
                self.assertEqual(ctx["role"], "integrator", base)
                r = pg.evaluate(ev("Read", file_path="seal_audit.jsonl"),
                                fresh_policy(), ctx)
                self.assertTrue(r["allowed"], base)

    def test_run_id_with_dash(self):
        run = self._run("my-run", [self._task("T2", "module-dev")])
        wt = os.path.join(self.tmp.name, "my-run-t2")
        os.makedirs(wt, exist_ok=True)
        ctx = pg.build_context(wt, run)
        self.assertEqual(ctx["role"], "module-dev")

    def test_wrong_prefix_no_match(self):
        run = self._run("rr", [self._task("I1")])
        wt = os.path.join(self.tmp.name, "other-i1")
        os.makedirs(wt, exist_ok=True)
        ctx = pg.build_context(wt, run)
        self.assertEqual(ctx["role"], "")
        r = pg.evaluate(ev("Read", file_path="seal_audit.jsonl"),
                        fresh_policy(), ctx)
        self.assertFalse(r["allowed"])

    def test_task_id_case_insensitive_env(self):
        run = self._run("rr", [self._task("I1")])
        os.environ["AI_PIPELINE_TASK"] = "i1"
        ctx = pg.build_context(self.cwd, run)
        self.assertEqual(ctx["role"], "integrator")


class TestProbeRegressionFH2(EnvScrub):
    """FH2-4: hoi quy probe coordinator (uv pip, bash -c ghep, findstr/dir)."""

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.cwd, "labels"))
        with open(os.path.join(self.cwd, "labels", "gold.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write('{"y": 1}')
        os.makedirs(os.path.join(self.cwd, "src"))
        with open(os.path.join(self.cwd, "src", "main.py"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write("print(1)\n")
        self.pol = fresh_policy(protected_paths=["labels/gold.json"])

    def check(self, event, policy=None, **ctxkw):
        return pg.evaluate(event, policy or self.pol, self.ctx(**ctxkw))

    def test_uv_pip_blocked(self):
        for c in ("uv pip install x", "uv pip sync x", "UV PIP INSTALL x"):
            with self.subTest(cmd=c):
                r = pg.evaluate(ev("Bash", command=c), fresh_policy(),
                                self.ctx())
                self.assertFalse(r["allowed"], c)
                self.assertEqual(r["rule"], "host_install")

    def test_chained_payload_blocked(self):
        for c in ('bash -c "cd x && pip install y"',
                  'bash -c "echo ok; pip install y"',
                  'bash -c "echo ok || pip install y"',
                  'sh -c "cd x; pip install y"',
                  'python -c "import os" && pip install y',
                  "echo hi | pip install x"):
            with self.subTest(cmd=c):
                r = pg.evaluate(ev("Bash", command=c), fresh_policy(),
                                self.ctx())
                self.assertFalse(r["allowed"], c)
                self.assertEqual(r["rule"], "host_install")

    def test_chained_payload_allowed_when_clean(self):
        r = pg.evaluate(ev("Bash", command='bash -c "cd x && echo ok"'),
                        fresh_policy(), self.ctx())
        self.assertTrue(r["allowed"])

    def test_findstr_no_path_blocked(self):
        r = self.check(ev("Bash", command="findstr /s foo"))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_findstr_no_recurse_allowed(self):
        r = self.check(ev("Bash", command="findstr foo src/main.py"))
        self.assertTrue(r["allowed"])

    def test_findstr_clean_dir_allowed(self):
        r = self.check(ev("Bash", command="findstr /s foo src"))
        self.assertTrue(r["allowed"])

    def test_dir_recursive_blocked(self):
        for c in ("dir /s", "dir /s .", "dir /s .."):
            with self.subTest(cmd=c):
                r = self.check(ev("Bash", command=c))
                self.assertFalse(r["allowed"], c)
                self.assertEqual(r["rule"], "label_protection")

    def test_dir_plain_allowed(self):
        r = self.check(ev("Bash", command="dir"))
        self.assertTrue(r["allowed"])
        r = self.check(ev("Bash", command="dir /s src"))
        self.assertTrue(r["allowed"])

    def test_select_string_ancestor_blocked(self):
        r = self.check(ev("Bash", command='Select-String -Recurse -Pattern foo -Path ..'))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["rule"], "label_protection")

    def test_specific_glob_exclude_allowed(self):
        pol = fresh_policy(protected_paths=["eval_manifest.json"])
        r = pg.evaluate(ev("Bash", command="rg foo . --glob '!eval_manifest.json'"),
                        pol, self.ctx())
        self.assertTrue(r["allowed"], r)
        r = pg.evaluate(ev("Bash", command="grep -rn foo . --exclude='eval_manifest.json'"),
                        pol, self.ctx())
        self.assertTrue(r["allowed"], r)

    def test_real_path_with_exclude_still_blocked(self):
        # exclude khong cuu duoc duong dan nhan that trong lenh khac
        pol = fresh_policy(protected_paths=["eval_manifest.json"])
        r = pg.evaluate(ev("Bash", command="cat eval_manifest.json; rg foo . --glob '!eval_manifest.json'"),
                        pol, self.ctx())
        self.assertFalse(r["allowed"])


if __name__ == "__main__":
    unittest.main()
