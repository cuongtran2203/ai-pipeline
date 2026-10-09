"""herdr_rt + worker_done: luong run-create -> task-create -> worker-start -> worker_done -> check, voi herdr gia (khong goi herdr that)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import herdr_rt  # noqa: E402
import worker_done  # noqa: E402


class FakeHerdr:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, *args, **kw):
        self.calls.append(args)
        if self.fail_on and args[:2] == tuple(self.fail_on):
            return 1, "", "boom"
        if args[:2] == ("workspace", "create"):
            return 0, json.dumps({"result": {"workspace_id": "ws1"}}), ""
        if args[:2] == ("tab", "create"):
            return 0, json.dumps({"result": {"pane_id": "p%d" % len(self.calls)}}), ""
        if args[:2] == ("agent", "get"):
            return 0, json.dumps({"status": "working"}), ""
        return 0, "{}", ""


class HerdrRt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rd = self.tmp.name
        self.fake = FakeHerdr()
        self._old = herdr_rt.herdr
        herdr_rt.herdr = self.fake
        self._sleep = herdr_rt.time.sleep
        herdr_rt.time.sleep = lambda s: None

    def tearDown(self):
        herdr_rt.herdr = self._old
        herdr_rt.time.sleep = self._sleep
        self.tmp.cleanup()

    def _start(self):
        run = herdr_rt.run_create(self.rd, "obj")["result"]
        tid = herdr_rt.task_create(self.rd, run["id"], "T", "SPEC BODY", [])["result"]["id"]
        s = herdr_rt.worker_start(self.rd, run["id"], tid, "current", "w1", "claude", "opus", None)
        return run, tid, s

    def test_run_create_idempotent(self):
        a = herdr_rt.run_create(self.rd, "o")["result"]["id"]
        self.assertEqual(a, herdr_rt.run_create(self.rd, "o")["result"]["id"])

    def test_worker_start_launches_in_own_pane_and_writes_prompt(self):
        run, tid, s = self._start()
        self.assertTrue(s["ok"], s)
        d = s["result"]["dispatchId"]
        prompt = open(os.path.join(self.rd, "workers", d + ".prompt.md"), encoding="utf-8").read()
        self.assertIn("SPEC BODY", prompt)
        self.assertIn(tid, prompt)
        verbs = [c[:2] for c in self.fake.calls]
        self.assertIn(("tab", "create"), verbs)
        self.assertEqual(verbs.count(("pane", "run")), 3)  # cd, setenv, agent
        self.assertIn(("pane", "send-text"), verbs)

    def test_worker_start_failure_is_clear_receipt(self):
        self.fake.fail_on = ("pane", "run")
        _, _, s = self._start()
        self.assertFalse(s["ok"])
        self.assertEqual(s["result"]["failedStage"], "launch")

    def test_done_then_check_and_ask(self):
        run, tid, s = self._start()
        worker_done.main([self.rd, "ask", "--task", tid, "--question", "G3?"])
        ev = herdr_rt.check(self.rd, False, 0, {"worker_done", "escalation", "question"})["result"]["events"]
        self.assertEqual([e["type"] for e in ev], ["question"])
        worker_done.main([self.rd, "answer", "--ask", tid + "-1", "--text", "yes"])
        worker_done.main([self.rd, "done", "--task", tid, "--outcome", "succeeded", "--report-path", "r.md"])
        ev = herdr_rt.check(self.rd, False, 0, {"worker_done", "escalation", "question"})["result"]["events"]
        self.assertEqual([(e["type"], e["worker"]) for e in ev], [("worker_done", s["result"]["dispatchId"])])
        worker_done.main([self.rd, "done", "--task", tid, "--outcome", "failed"])
        ev = herdr_rt.check(self.rd, False, 0, {"worker_done", "escalation"})["result"]["events"]
        self.assertEqual(ev[0]["type"], "escalation")

    def test_worker_list_and_release(self):
        run, tid, s = self._start()
        rows = herdr_rt.worker_list(self.rd)["result"]["workers"]
        self.assertEqual(rows[0]["agentStatus"], "working")
        herdr_rt.worker_release(self.rd, s["result"]["dispatchId"])
        self.assertTrue(herdr_rt.worker_list(self.rd)["result"]["workers"][0]["released"])
        self.assertIn(("pane", "close"), [c[:2] for c in self.fake.calls])


if __name__ == "__main__":
    unittest.main()
