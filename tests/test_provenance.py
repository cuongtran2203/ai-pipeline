#!/usr/bin/env python3
"""Unittest stdlib cho scripts/data_provenance.py (truy vet dataset ngoai).

Phu: schema dataset card, register/approve/verify/list/use-check du nhanh tu choi
(chua duyet, giay phep sai, thieu domain-shift, hash lech, approver agent/rong), idempotent,
KG VALID (Artifact+Decision+Person), dua ghi registry an toan (khong mat entry), khong mang.

Test co the chay tren ban sao bi mutation bang bien moi truong DATA_PROVENANCE_PATH
(xem runs/ai-pipeline-v2/artifacts/OP2/mutation_probe.py).

Chay: python -m unittest discover -s tests -v
"""
import hashlib
import importlib.util
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import kg  # noqa: E402
import statefile  # noqa: E402

MODULE_PATH = os.environ.get("DATA_PROVENANCE_PATH", os.path.join(SCRIPTS, "data_provenance.py"))
MODULE_PATH = os.path.abspath(MODULE_PATH)
_REGISTRY = ["external_data", "registry.json"]


def _load_module(path):
    path = os.path.abspath(path)
    name = "data_provenance_ut_" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:10]
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dp = _load_module(MODULE_PATH)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _card(**over):
    c = {
        "name": "FakeDigits",
        "version": "ds-ext-fake-v1",
        "source_url": "https://example.invalid/digits",
        "license": "CC0-1.0",
        "license_ok": True,
        "retrieved_at": "2026-01-01T00:00:00Z",
        "domain": "handwritten digits, nguon tong hop",
        "intended_use": "train digit classifier (khong vao val/test)",
        "domain_shift_assessment": "nguon khac; kiem bang thi nghiem nho tren val that",
        "location": None,
        "size_bytes": 10,
        "archive_sha256": "a" * 64,
    }
    c.update(over)
    return c


def _register_worker(module_path, run_dir, card, q):
    mod = _load_module(module_path)
    try:
        mod.register(run_dir, card, actor="researcher")
        q.put(None)
    except Exception as e:  # noqa: BLE001
        q.put(f"{type(e).__name__}: {e}")


class ProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp(prefix="prov-test-")
        self.addCleanup(shutil.rmtree, self.run, ignore_errors=True)
        self.work = tempfile.mkdtemp(prefix="prov-data-")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    # -- helpers -------------------------------------------------------------
    def reg_path(self):
        return os.path.join(self.run, *_REGISTRY)

    def make_file(self, name="data.bin", content=b"hello-digits"):
        p = os.path.join(self.work, name)
        with open(p, "wb") as f:
            f.write(content)
        return p

    def make_dir(self, files):
        d = os.path.join(self.work, "dataset")
        os.makedirs(d, exist_ok=True)
        for name, content in files.items():
            p = os.path.join(d, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(content)
        manifest = {}
        for root, dirs, fs in os.walk(d):
            for fn in fs:
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, d).replace("\\", "/")
                manifest[rel] = _sha256_file(fp)
        return d, manifest

    def register_ok(self, **over):
        p = over.pop("location", None) or self.make_file()
        card = _card(location=p, archive_sha256=over.pop("archive_sha256", _sha256_file(p)), **over)
        entry, created = dp.register(self.run, card, actor="researcher")
        return entry, created

    def entries(self):
        return dp.load_registry(self.run)["entries"]

    # -- schema --------------------------------------------------------------
    def test_register_rejects_missing_domain_shift(self):
        c = _card()
        del c["domain_shift_assessment"]
        with self.assertRaises(dp.ProvenanceError):
            dp.register(self.run, c)
        self.assertEqual(self.entries(), {})

    def test_register_rejects_empty_domain_shift(self):
        with self.assertRaises(dp.ProvenanceError):
            dp.register(self.run, _card(domain_shift_assessment="   "))

    def test_register_rejects_missing_hash(self):
        c = _card()
        del c["archive_sha256"]
        with self.assertRaises(dp.ProvenanceError):
            dp.register(self.run, c)
        self.assertEqual(self.entries(), {})

    def test_register_rejects_bad_archive_hash(self):
        with self.assertRaises(dp.ProvenanceError):
            dp.register(self.run, _card(archive_sha256="xyz"))

    def test_register_rejects_bad_manifest_hash(self):
        with self.assertRaises(dp.ProvenanceError):
            dp.register(self.run, _card(archive_sha256=None, manifest={"a.bin": "nope"}))

    def test_register_rejects_non_bool_license_ok(self):
        with self.assertRaises(dp.ProvenanceError):
            dp.register(self.run, _card(license_ok="yes"))

    def test_register_rejects_missing_name(self):
        c = _card()
        del c["name"]
        with self.assertRaises(dp.ProvenanceError):
            dp.register(self.run, c)

    def test_register_requires_version(self):
        c = _card()
        del c["version"]
        with self.assertRaises(dp.ProvenanceError):
            dp.register(self.run, c)

    # -- register ------------------------------------------------------------
    def test_register_creates_entry_with_derived_id(self):
        entry, created = self.register_ok()
        self.assertTrue(created)
        self.assertEqual(entry["id"], "ext-fakedigits-ds-ext-fake-v1")
        self.assertEqual(entry["approved_by"], None)
        self.assertIn(entry["id"], self.entries())

    def test_register_is_idempotent(self):
        p = self.make_file()
        card = _card(location=p, archive_sha256=_sha256_file(p))
        _, created1 = dp.register(self.run, card)
        entry2, created2 = dp.register(self.run, card)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(entry2["registered_at"], self.entries()[entry2["id"]]["registered_at"])
        # idempotent => khong them dong so thi nghiem trung
        journal = statefile.read_jsonl(os.path.join(self.run, "notebook", "journal.jsonl"))
        self.assertEqual(len([e for e in journal if "Dang ky dataset ngoai" in e.get("title", "")]), 1)

    def test_register_conflicting_content_same_id_rejected(self):
        p = self.make_file()
        card = _card(id="fixed-id", location=p, archive_sha256=_sha256_file(p))
        dp.register(self.run, card)
        with self.assertRaises(dp.ProvenanceError):
            dp.register(self.run, _card(id="fixed-id", location=p, name="Khac Ten",
                                        archive_sha256=_sha256_file(p)))

    def test_registry_written_utf8_no_bom(self):
        self.register_ok()
        with open(self.reg_path(), "rb") as f:
            raw = f.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", raw)

    # -- approve -------------------------------------------------------------
    def test_approve_rejects_empty(self):
        entry, _ = self.register_ok()
        with self.assertRaises(dp.ProvenanceError):
            dp.approve(self.run, entry["id"], "")
        with self.assertRaises(dp.ProvenanceError):
            dp.approve(self.run, entry["id"], "  ")

    def test_approve_rejects_agent_without_person_prefix(self):
        entry, _ = self.register_ok()
        for bad in ("codex", "agent:codex", "module-dev", "claude", "Alice"):
            with self.assertRaises(dp.ProvenanceError):
                dp.approve(self.run, entry["id"], bad)

    def test_approve_rejects_empty_person_name(self):
        entry, _ = self.register_ok()
        with self.assertRaises(dp.ProvenanceError):
            dp.approve(self.run, entry["id"], "person:")

    def test_approve_rejects_agent_like_name(self):
        entry, _ = self.register_ok()
        with self.assertRaises(dp.ProvenanceError):
            dp.approve(self.run, entry["id"], "person:agent-codex")

    def test_approve_accepts_person(self):
        entry, _ = self.register_ok()
        out, changed = dp.approve(self.run, entry["id"], "person:Nguyen Van A")
        self.assertTrue(changed)
        self.assertEqual(out["approved_by"], "person:Nguyen Van A")
        self.assertTrue(out["approved_at"])

    def test_approve_unknown_id(self):
        with self.assertRaises(dp.ProvenanceError):
            dp.approve(self.run, "missing", "person:An")

    def test_approve_is_idempotent_for_same_person(self):
        entry, _ = self.register_ok()
        dp.approve(self.run, entry["id"], "person:An")
        out, changed = dp.approve(self.run, entry["id"], "person:An")
        self.assertFalse(changed)
        self.assertEqual(out["approved_by"], "person:An")

    def test_approve_by_other_person_updates(self):
        entry, _ = self.register_ok()
        dp.approve(self.run, entry["id"], "person:An")
        out, changed = dp.approve(self.run, entry["id"], "person:Binh")
        self.assertTrue(changed)
        self.assertEqual(out["approved_by"], "person:Binh")

    # -- use-check -----------------------------------------------------------
    def test_use_check_unknown_id(self):
        res = dp.use_check(self.run, "missing")
        self.assertFalse(res["ok"])

    def test_use_check_blocks_not_approved(self):
        entry, _ = self.register_ok()
        res = dp.use_check(self.run, entry["id"])
        self.assertFalse(res["ok"])
        self.assertIn("CHUA duoc nguoi duyet", res["reason"])

    def test_use_check_blocks_bad_license(self):
        p = self.make_file()
        entry, _ = dp.register(self.run, _card(location=p, license_ok=False,
                                               archive_sha256=_sha256_file(p)))
        dp.approve(self.run, entry["id"], "person:An")
        res = dp.use_check(self.run, entry["id"])
        self.assertFalse(res["ok"])
        self.assertIn("license_ok", res["reason"])

    def test_use_check_blocks_missing_domain_shift(self):
        entry, _ = self.register_ok()
        dp.approve(self.run, entry["id"], "person:An")

        def strip(data):
            data["entries"][entry["id"]].pop("domain_shift_assessment", None)
            return data

        statefile.update_json(self.reg_path(), strip)
        res = dp.use_check(self.run, entry["id"])
        self.assertFalse(res["ok"])
        self.assertIn("domain_shift_assessment", res["reason"])

    def test_use_check_blocks_hash_mismatch_registered_wrong(self):
        p = self.make_file()
        entry, _ = dp.register(self.run, _card(location=p, archive_sha256="b" * 64))
        dp.approve(self.run, entry["id"], "person:An")
        res = dp.use_check(self.run, entry["id"])
        self.assertFalse(res["ok"])
        self.assertIn("hash khong khop", res["reason"])

    def test_use_check_blocks_hash_mismatch_after_change(self):
        entry, _ = self.register_ok()
        dp.approve(self.run, entry["id"], "person:An")
        with open(entry["location"], "wb") as f:
            f.write(b"tampered")
        res = dp.use_check(self.run, entry["id"])
        self.assertFalse(res["ok"])

    def test_use_check_ok(self):
        entry, _ = self.register_ok()
        dp.approve(self.run, entry["id"], "person:An")
        res = dp.use_check(self.run, entry["id"])
        self.assertTrue(res["ok"], res.get("reason"))

    # -- verify --------------------------------------------------------------
    def test_verify_ok_file(self):
        entry, _ = self.register_ok()
        res = dp.verify(self.run, entry["id"])
        self.assertTrue(res["ok"], res.get("reason"))

    def test_verify_reports_mismatch(self):
        entry, _ = self.register_ok()
        with open(entry["location"], "wb") as f:
            f.write(b"changed")
        res = dp.verify(self.run, entry["id"])
        self.assertFalse(res["ok"])
        self.assertIn("khong khop", res["reason"])

    def test_verify_missing_location(self):
        entry, _ = dp.register(self.run, _card(location=os.path.join(self.work, "nope.bin")))
        res = dp.verify(self.run, entry["id"])
        self.assertFalse(res["ok"])
        self.assertIn("khong ton tai", res["reason"])

    def test_verify_dir_manifest_ok(self):
        d, manifest = self.make_dir({"a/one.bin": b"1", "two.bin": b"2"})
        entry, _ = dp.register(self.run, _card(location=d, archive_sha256=None, manifest=manifest))
        res = dp.verify(self.run, entry["id"])
        self.assertTrue(res["ok"], res.get("reason"))

    def test_verify_dir_manifest_reports_changed_file(self):
        d, manifest = self.make_dir({"a/one.bin": b"1", "two.bin": b"2"})
        entry, _ = dp.register(self.run, _card(location=d, archive_sha256=None, manifest=manifest))
        with open(os.path.join(d, "two.bin"), "wb") as f:
            f.write(b"2-changed")
        res = dp.verify(self.run, entry["id"])
        self.assertFalse(res["ok"])
        self.assertIn("two.bin", res["changed"])

    def test_verify_dir_extra_file_reported(self):
        d, manifest = self.make_dir({"a.bin": b"1"})
        entry, _ = dp.register(self.run, _card(location=d, archive_sha256=None, manifest=manifest))
        with open(os.path.join(d, "b.bin"), "wb") as f:
            f.write(b"2")
        res = dp.verify(self.run, entry["id"])
        self.assertFalse(res["ok"])
        self.assertIn("b.bin", res["extra"])

    # -- knowledge graph -----------------------------------------------------
    def test_kg_valid_and_has_artifact_decision_person(self):
        entry, _ = self.register_ok()
        dp.approve(self.run, entry["id"], "person:Nguyen Van A")
        _entities, _edges, errors, _warnings = kg.collect_validation(self.run)
        self.assertEqual(errors, [], errors)
        entities = kg.read_entities(self.run)
        art_id = "artifact:extdata-" + entry["id"]
        self.assertIn(art_id, entities)
        self.assertEqual(entities[art_id]["type"], "Artifact")
        reg_dec = "decision:extdata-register-" + entry["id"]
        app_dec = "decision:extdata-approve-" + entry["id"]
        self.assertEqual(entities[reg_dec]["type"], "Decision")
        self.assertEqual(entities[app_dec]["type"], "Decision")
        edges = {(e["source"], e["target"], e["type"]) for e in kg.read_edges(self.run)}
        self.assertIn((reg_dec, "person:researcher", "decided_by"), edges)
        self.assertIn((app_dec, "person:nguyen-van-a", "approved_by"), edges)
        self.assertIn((app_dec, reg_dec, "supersedes"), edges)

    # -- concurrency ---------------------------------------------------------
    def test_concurrent_register_does_not_lose_entries(self):
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        procs = []
        for i in range(6):
            p = os.path.join(self.work, f"f{i}.bin")
            with open(p, "wb") as f:
                f.write(f"payload-{i}".encode())
            card = _card(name=f"Data{i}", location=p, archive_sha256=_sha256_file(p))
            proc = ctx.Process(target=_register_worker, args=(MODULE_PATH, self.run, card, q))
            proc.start()
            procs.append(proc)
        for proc in procs:
            proc.join(60)
            self.assertFalse(proc.is_alive(), "tien trinh con chua ket thuc")
        results = [q.get() for _ in procs]
        self.assertEqual([r for r in results if r], [], results)
        entries = self.entries()
        self.assertEqual(len(entries), 6, sorted(entries))

    # -- no network ----------------------------------------------------------
    def test_module_does_not_use_network(self):
        with open(MODULE_PATH, encoding="utf-8") as f:
            src = f.read()
        for token in ("urllib", "requests", "socket", "http.client", "ftplib", "urlopen"):
            self.assertNotIn(token, src, f"data_provenance.py khong duoc dung mang: {token}")

    # -- CLI -----------------------------------------------------------------
    def run_cli(self, *args):
        return subprocess.run([sys.executable, MODULE_PATH, *args],
                              capture_output=True, text=True, encoding="utf-8", cwd=ROOT)

    def card_file(self, card):
        p = os.path.join(self.work, "card.json")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(card, f, ensure_ascii=False)
        return p

    def test_cli_full_flow(self):
        data = self.make_file()
        cf = self.card_file(_card(location=data, archive_sha256=_sha256_file(data)))
        r = self.run_cli("register", self.run, "--card", cf)
        self.assertEqual(r.returncode, 0, r.stderr)
        entry_id = None
        for line in reversed(r.stdout.splitlines()):
            try:
                entry_id = json.loads(line)["id"]
                break
            except ValueError:
                continue
        self.assertIsNotNone(entry_id, r.stdout)

        r = self.run_cli("use-check", self.run, entry_id)
        self.assertEqual(r.returncode, 1)
        r = self.run_cli("approve", self.run, entry_id, "--approver", "agent:codex")
        self.assertEqual(r.returncode, 2)
        r = self.run_cli("approve", self.run, entry_id, "--approver", "person:An")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("use-check", self.run, entry_id)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("verify", self.run, entry_id)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("list", self.run, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.loads(r.stdout)), 1)

    def test_cli_register_missing_domain_shift_returns_2(self):
        data = self.make_file()
        c = _card(location=data, archive_sha256=_sha256_file(data))
        del c["domain_shift_assessment"]
        cf = self.card_file(c)
        r = self.run_cli("register", self.run, "--card", cf)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
