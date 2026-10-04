#!/usr/bin/env python3
"""Unittest stdlib cho scripts/monitor.py: control band (huong, sigma=0, min_n, stale),
drift PSI/KS, incident -> KG, dismiss, metrics hong, policy fail-closed, idempotent, mutation.

Chay: python -m unittest discover -s tests -v
"""
import importlib.util
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
MONITOR = os.path.join(SCRIPTS, "monitor.py")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import monitor  # noqa: E402

BANDS = {"warn": 1.0, "diagnose": 2.0, "propose": 3.0}


def run_cli(*args):
    return subprocess.run([sys.executable, MONITOR, *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=ROOT)


def write_policy(run, metrics, **extra):
    pol = {"run_id": "t", "policy_version": "v1", "metrics": metrics}
    pol.update(extra)
    with io.open(os.path.join(run, "monitor_policy.json"), "w", encoding="utf-8") as f:
        json.dump(pol, f, ensure_ascii=False)


def rec(run, metric, value, n=None, labels=None, ts=None):
    return monitor.write_record(run, metric, value, n, labels or {}, ts)


def tmp_run(testcase):
    d = tempfile.mkdtemp(prefix="mon-test-")
    testcase.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return d


class BandTests(unittest.TestCase):
    def test_higher_is_better_direction(self):
        entry = {"direction": "higher_is_better", "baseline": {"values": [0.95, 0.96, 0.94]}, "bands": BANDS}
        worse = monitor.evaluate_entry(entry, 0.80, 100, [])
        better = monitor.evaluate_entry(entry, 0.99, 100, [])
        self.assertEqual(worse["tier"], "propose")
        self.assertEqual(better["tier"], "ok")

    def test_lower_is_better_direction(self):
        entry = {"direction": "lower_is_better", "baseline": {"values": [100, 102, 98]}, "bands": BANDS}
        worse = monitor.evaluate_entry(entry, 500, 100, [])
        better = monitor.evaluate_entry(entry, 90, 100, [])
        self.assertEqual(worse["tier"], "propose")
        self.assertEqual(better["tier"], "ok")

    def test_sigma_zero_no_zero_division(self):
        entry = {"direction": "higher_is_better", "baseline": {"values": [0.9, 0.9, 0.9]}, "bands": BANDS}
        same = monitor.evaluate_entry(entry, 0.9, 100, [])
        changed = monitor.evaluate_entry(entry, 0.8, 100, [])
        self.assertEqual(same["tier"], "ok")
        self.assertEqual(changed["tier"], "propose")
        self.assertEqual(changed["band"], "σ=0")

    def test_min_n_caps_high_tier(self):
        entry = {"direction": "higher_is_better", "baseline": {"values": [0.95, 0.96, 0.94]},
                 "bands": BANDS, "min_n": 30}
        capped = monitor.evaluate_entry(entry, 0.80, 5, [])
        full = monitor.evaluate_entry(entry, 0.80, 100, [])
        self.assertEqual(full["tier"], "propose")
        self.assertEqual(capped["tier"], "warn")

    def test_absolute_bands(self):
        entry = {"direction": "lower_is_better", "baseline": {"value": 0.0},
                 "bands_abs": {"warn": 0.1, "diagnose": 0.2, "propose": 0.25}}
        self.assertEqual(monitor.evaluate_entry(entry, 0.05, 100, [])["tier"], "ok")
        self.assertEqual(monitor.evaluate_entry(entry, 0.15, 100, [])["tier"], "warn")
        self.assertEqual(monitor.evaluate_entry(entry, 0.3, 100, [])["tier"], "propose")

    def test_baseline_single_value_no_crash(self):
        entry = {"direction": "higher_is_better", "baseline": {"window": 1}, "bands": BANDS}
        res = monitor.evaluate_entry(entry, 0.5, 100, [{"value": 0.5}])
        self.assertEqual(res["tier"], "ok")


class DriftTests(unittest.TestCase):
    def test_psi_categorical_manual(self):
        psi = monitor.psi_categorical(["A", "A", "A", "B"], ["A", "B", "B", "B"])
        self.assertAlmostEqual(psi, math.log(3), places=4)

    def test_psi_numeric_identical_is_zero(self):
        self.assertEqual(monitor.psi_numeric([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]), 0.0)

    def test_ks_manual(self):
        d, p = monitor.ks_two_sample([1, 2, 3, 4], [2, 3, 4, 5])
        self.assertAlmostEqual(d, 0.25, places=6)
        self.assertTrue(0.0 <= p <= 1.0)

    def test_ks_identical_is_zero(self):
        d, p = monitor.ks_two_sample([1, 2, 3], [1, 2, 3])
        self.assertEqual(d, 0.0)
        self.assertEqual(p, 1.0)

    def test_drift_cli_json(self):
        run = tmp_run(self)
        ref = os.path.join(run, "ref.json")
        cur = os.path.join(run, "cur.json")
        with io.open(ref, "w", encoding="utf-8") as f:
            json.dump(["A", "A", "A", "B"], f)
        with io.open(cur, "w", encoding="utf-8") as f:
            json.dump(["A", "B", "B", "B"], f)
        r = run_cli("drift", "--ref", ref, "--cur", cur, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertAlmostEqual(out["psi"], math.log(3), places=4)


KG = os.path.join(SCRIPTS, "kg.py")


def kg_validate(run):
    return subprocess.run([sys.executable, KG, "validate", run],
                          capture_output=True, text=True, encoding="utf-8", cwd=ROOT)


class CheckFlowTests(unittest.TestCase):
    def _setup_run(self, metric="accuracy", direction="higher_is_better", stale=None,
                   base=(0.95, 0.96, 0.94), cur=0.80, n=100, cur_ts="2026-10-02T00:00:00Z"):
        run = tmp_run(self)
        extra = {"stale_after_minutes": stale} if stale else {}
        write_policy(run, {metric: {"direction": direction, "baseline": {"window": len(base)},
                                    "min_n": 1, "bands": BANDS}}, **extra)
        for v in base:
            rec(run, metric, v, 100, {"model_version": "rec-v1"}, "2026-10-01T00:00:00Z")
        rec(run, metric, cur, n, {"model_version": "rec-v1"}, cur_ts)
        return run

    def test_check_incident_kg_valid_and_state(self):
        run = self._setup_run()
        r = run_cli("check", run, "--now", "2026-10-02T01:00:00Z")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        st = monitor.statefile.read_json(monitor.state_path(run))
        self.assertEqual(st["metrics"]["accuracy"]["tier"], "propose")
        self.assertEqual(len(os.listdir(monitor.incidents_dir(run))), 1)
        v = kg_validate(run)
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("VALID", v.stdout)

    def test_check_idempotent(self):
        run = self._setup_run()
        run_cli("check", run, "--now", "2026-10-02T01:00:00Z")
        run_cli("check", run, "--now", "2026-10-02T02:00:00Z")
        self.assertEqual(len(monitor.statefile.read_jsonl(monitor.actions_path(run))), 1)
        self.assertEqual(len(monitor.statefile.read_jsonl(monitor.incidents_registry(run))), 1)

    def test_stale_signal(self):
        run = self._setup_run(stale=60, cur=0.95, cur_ts="2026-10-02T00:00:00Z")
        r = run_cli("check", run, "--now", "2026-10-03T00:00:00Z")
        self.assertEqual(r.returncode, 2, r.stderr)
        st = monitor.statefile.read_json(monitor.state_path(run))
        self.assertEqual(st["metrics"]["accuracy"]["tier"], "stale")

    def test_no_data_metric(self):
        run = tmp_run(self)
        write_policy(run, {"f1": {"direction": "higher_is_better", "baseline": {"values": [0.9]},
                                  "bands": BANDS}})
        r = run_cli("check", run, "--now", "2026-10-02T01:00:00Z")
        self.assertEqual(r.returncode, 1, r.stderr)
        out = json.loads(run_cli("check", run, "--now", "2026-10-02T01:00:00Z", "--json").stdout)
        self.assertEqual(out["metrics"][0]["tier"], "no_data")


class CliErrorTests(unittest.TestCase):
    def test_policy_invalid_fail_closed(self):
        run = tmp_run(self)
        write_policy(run, {"accuracy": {"baseline": {"values": [0.9]}, "bands": BANDS}})
        r = run_cli("check", run, "--now", "2026-10-02T01:00:00Z")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("direction", r.stderr)

    def test_policy_missing_fail_closed(self):
        run = tmp_run(self)
        r = run_cli("check", run, "--now", "2026-10-02T01:00:00Z")
        self.assertEqual(r.returncode, 3)
        self.assertIn("monitor_policy", r.stderr)

    def test_metrics_corrupt_mid_file_reports_clearly(self):
        run = tmp_run(self)
        write_policy(run, {"accuracy": {"direction": "higher_is_better", "baseline": {"values": [0.9]},
                                        "bands": BANDS}})
        mp = monitor.metrics_path(run)
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        with io.open(mp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"ts": "2026-10-01T00:00:00Z", "metric": "accuracy", "value": 0.9, "labels": {}, "n": 10}) + "\n")
            f.write("khong-phai-json\n")
            f.write(json.dumps({"ts": "2026-10-02T00:00:00Z", "metric": "accuracy", "value": 0.8, "labels": {}, "n": 10}) + "\n")
        r = run_cli("check", run, "--now", "2026-10-02T01:00:00Z")
        self.assertEqual(r.returncode, 3)
        self.assertIn("hỏng", r.stderr)

    def test_dismiss_requires_reason_and_is_append_only(self):
        run = tempfile.mkdtemp(prefix="mon-dism-")
        self.addCleanup(shutil.rmtree, run, ignore_errors=True)
        write_policy(run, {"accuracy": {"direction": "higher_is_better", "baseline": {"window": 3},
                                        "min_n": 1, "bands": BANDS}})
        for v in (0.95, 0.96, 0.94):
            rec(run, "accuracy", v, 100, {}, "2026-10-01T00:00:00Z")
        rec(run, "accuracy", 0.80, 100, {}, "2026-10-02T00:00:00Z")
        run_cli("check", run, "--now", "2026-10-02T01:00:00Z")
        reg = monitor.statefile.read_jsonl(monitor.incidents_registry(run))
        self.assertEqual(len(reg), 1)
        inc = reg[0]["id"]
        no_reason = run_cli("dismiss", run, inc)
        self.assertNotEqual(no_reason.returncode, 0)
        ok = run_cli("dismiss", run, inc, "--reason", "báo động giả do mùa vụ")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        events = monitor.statefile.read_jsonl(monitor.incident_events_path(run))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "báo động giả do mùa vụ")
        listed = run_cli("list", run)
        self.assertIn(inc, listed.stdout)
        self.assertIn("dismiss", listed.stdout)

    def test_record_cli_writes_metric(self):
        run = tmp_run(self)
        r = run_cli("record", run, "--metric", "latency_p95", "--value", "123.5",
                    "--n", "50", "--label", "model_version=rec-v1", "--label", "slice=hw")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = monitor.read_metrics(run)
        self.assertEqual(rows[0]["metric"], "latency_p95")
        self.assertEqual(rows[0]["labels"], {"model_version": "rec-v1", "slice": "hw"})
        self.assertEqual(rows[0]["n"], 50)


class MutationTests(unittest.TestCase):
    def _load_mutated(self, old, new):
        with io.open(MONITOR, encoding="utf-8") as _f:
            src = _f.read()
        self.assertIn(old, src)
        mutated = src.replace(old, new, 1)
        d = tempfile.mkdtemp(prefix="mon-mut-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, "monitor_mutated.py")
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(mutated)
        spec = importlib.util.spec_from_file_location("monitor_mutated", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_direction_mutation_is_detected(self):
        entry = {"direction": "higher_is_better", "baseline": {"values": [0.95, 0.96, 0.94]}, "bands": BANDS}
        real = monitor.evaluate_entry(entry, 0.80, 100, [])
        mutated = self._load_mutated(
            '(center - value) if direction == "higher_is_better" else (value - center)',
            '(value - center) if direction == "higher_is_better" else (center - value)')
        flipped = mutated.evaluate_entry(entry, 0.80, 100, [])
        self.assertEqual(real["tier"], "propose")
        self.assertNotEqual(flipped["tier"], "propose")

    def test_min_n_mutation_is_detected(self):
        entry = {"direction": "higher_is_better", "baseline": {"values": [0.95, 0.96, 0.94]},
                 "bands": BANDS, "min_n": 30}
        real = monitor.evaluate_entry(entry, 0.80, 5, [])
        mutated = self._load_mutated(
            'if mn and n is not None and n < mn and tier in ("diagnose", "propose"):',
            'if False and mn and n is not None and n < mn and tier in ("diagnose", "propose"):')
        uncapped = mutated.evaluate_entry(entry, 0.80, 5, [])
        self.assertEqual(real["tier"], "warn")
        self.assertEqual(uncapped["tier"], "propose")


if __name__ == "__main__":
    unittest.main()


