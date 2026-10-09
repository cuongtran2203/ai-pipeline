#!/usr/bin/env python3
"""Test scripts/diagram.py: render/validate/from-plan/from-model (stdlib only).

Moi hanh vi co test FAIL tren code cu (chua co diagram.py / chua ho tro),
PASS sau. Test dung thu muc tam, KHONG ghi vao run that.
"""
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import diagram  # noqa: E402

REAL_PLAN = r"C:\Users\24h\Desktop\AI_worklflow\runs\timesheet-ocr\artifacts\P4\plan.json"

SAMPLE = {
    "title": "Thu nghiem tieng Viet: det co dau",
    "direction": "LR",
    "nodes": [
        {"id": "anh", "label": "Ảnh vào có dấu: phở & <bò>", "kind": "data", "note": "ts-v1"},
        {"id": "g2", "label": "Cổng G2 duyệt?", "kind": "human-gate"},
        {"id": "rec", "label": "Nhận dạng", "kind": "ai", "note": "rec-v0.2"},
    ],
    "edges": [
        {"from": "anh", "to": "g2"},
        {"from": "g2", "to": "rec", "label": "đạt & duyệt"},
    ],
}


def load_mutant(old, new):
    """Ban sao tam cua diagram.py voi 1 logic loi bi hoan tac; tra ve module."""
    src = open(os.path.join(ROOT, "scripts", "diagram.py"), encoding="utf-8").read()
    assert old in src, "mutation anchor khong ton tai: %r" % old[:60]
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "diagram_mut.py")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(src.replace(old, new, 1))
    spec = importlib.util.spec_from_file_location("diagram_mut", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def good_doc():
    spec = diagram.spec_from_json(copy.deepcopy(SAMPLE))
    pos, W, H = diagram.layout(spec)
    return diagram.build_excalidraw(spec, pos), spec, pos, W, H


class TestRender(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_render_ba_dinh_dang_hop_le(self):
        out = os.path.join(self.tmp, "d")
        spec_path = os.path.join(self.tmp, "spec.json")
        with open(spec_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(SAMPLE, f, ensure_ascii=False)
        written = diagram.render_spec(diagram.load_json(spec_path), out,
                                      ["excalidraw", "svg", "obsidian"], "mau")
        self.assertEqual(len(written), 3)
        doc = json.load(open(os.path.join(out, "mau.excalidraw"), encoding="utf-8"))
        self.assertEqual(diagram.validate_doc(doc), [])
        svg = open(os.path.join(out, "mau.svg"), encoding="utf-8").read()
        self.assertIn("<svg", svg)
        md = open(os.path.join(out, "mau.excalidraw.md"), encoding="utf-8").read()
        self.assertIn("# Text Elements", md)
        self.assertIn("# Drawing", md)

    def test_render_tu_plan_that_copy_trong_tmp(self):
        if not os.path.isfile(REAL_PLAN):
            self.skipTest("thieu plan that timesheet-ocr")
        cp = os.path.join(self.tmp, "plan.json")
        shutil.copyfile(REAL_PLAN, cp)  # copy, KHONG sua run that
        out = os.path.join(self.tmp, "plan")
        written = diagram.render_spec(diagram.plan_to_spec(diagram.load_json(cp)),
                                      out, ["excalidraw", "svg", "obsidian"], "plan")
        self.assertTrue(any(p.endswith(".svg") for p in written))
        for p in written:
            if p.endswith(".excalidraw"):
                doc = json.load(open(p, encoding="utf-8"))
                self.assertEqual(diagram.validate_doc(doc), [], p)

    def test_from_model_tensor_va_lap(self):
        ms = json.load(open(os.path.join(ROOT, "templates", "model_spec.example.json"),
                            encoding="utf-8"))
        out = os.path.join(self.tmp, "m")
        written = diagram.render_spec(diagram.model_to_spec(ms), out,
                                      ["excalidraw", "svg"], "digit")
        self.assertEqual(len(written), 2)
        svg = open(os.path.join(out, "digit.svg"), encoding="utf-8").read()
        self.assertIn("[N,1,28,28]", svg)  # tensor o nhan phu
        self.assertIn("x2", svg)  # khoi lap
        doc = json.load(open(os.path.join(out, "digit.excalidraw"), encoding="utf-8"))
        self.assertEqual(diagram.validate_doc(doc), [])

    def test_tach_so_do_khi_vuot_25_node(self):
        nodes = [{"id": "n%02d" % i, "label": "Nut %d" % i, "kind": "service"}
                 for i in range(30)]
        edges = [{"from": "n%02d" % i, "to": "n%02d" % (i + 1)} for i in range(29)]
        spec = {"title": "lon", "direction": "LR", "nodes": nodes, "edges": edges}
        parts = diagram.split_spec(spec)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p["nodes"]), 25)
        out = os.path.join(self.tmp, "big")
        written = diagram.render_spec(spec, out, ["excalidraw", "svg"], "big")
        self.assertGreater(len([w for w in written if w.endswith(".excalidraw")]), 1)

    def test_chu_viet_co_dau_trong_svg(self):
        spec = diagram.spec_from_json(copy.deepcopy(SAMPLE))
        pos, W, H = diagram.layout(spec)
        svg = diagram.build_svg(spec, pos, W, H)
        self.assertIn("Ảnh vào có dấu", svg)  # UTF-8 nguyen ven
        self.assertIn("phở &amp;", svg)  # escape XML (nhan co the xuong dong)
        self.assertIn("&lt;bò&gt;", svg)
        self.assertNotIn("<font", svg)

    def test_obsidian_text_elements_khop(self):
        doc, spec, pos, W, H = good_doc()
        md = diagram.build_obsidian_md(spec, doc)
        for n in spec["nodes"]:
            self.assertIn(n["label"].split("&")[0].strip()[:6], md)
        self.assertIn("```json", md)
        back = diagram.extract_doc(md)
        self.assertIsNotNone(back)
        self.assertEqual(diagram.validate_doc(back), [])


class TestValidate(unittest.TestCase):
    def test_hop_le(self):
        doc, _, _, _, _ = good_doc()
        self.assertEqual(diagram.validate_doc(doc), [])

    def test_bat_thieu_binding(self):
        doc, _, _, _, _ = good_doc()
        txt = next(el for el in doc["elements"] if el["type"] == "text")
        txt.pop("containerId")
        errs = diagram.validate_doc(doc)
        self.assertTrue(any("containerId" in e for e in errs), errs)

    def test_bat_binding_mot_chieu(self):
        doc, _, _, _, _ = good_doc()
        shape = next(el for el in doc["elements"] if el["type"] == "rectangle")
        shape["boundElements"] = []
        errs = diagram.validate_doc(doc)
        self.assertTrue(any("hai chieu" in e for e in errs), errs)

    def test_bat_diamond(self):
        doc, _, _, _, _ = good_doc()
        shape = next(el for el in doc["elements"] if el["type"] == "rectangle")
        shape["type"] = "diamond"
        errs = diagram.validate_doc(doc)
        self.assertTrue(any("diamond" in e for e in errs), errs)

    def test_bat_mui_ten_lech_mep(self):
        doc, _, _, _, _ = good_doc()
        ar = next(el for el in doc["elements"] if el["type"] == "arrow")
        ar["points"][1][0] += 10  # lech 10px khoi mep
        errs = diagram.validate_doc(doc)
        self.assertTrue(any("lech mep" in e for e in errs), errs)

    def test_bat_id_trung(self):
        doc, _, _, _, _ = good_doc()
        doc["elements"].append(copy.deepcopy(doc["elements"][0]))
        errs = diagram.validate_doc(doc)
        self.assertTrue(any("trung" in e for e in errs), errs)

    def test_bat_mui_ten_khong_vuong_goc(self):
        doc, _, _, _, _ = good_doc()
        ar = next(el for el in doc["elements"] if el["type"] == "arrow")
        ar["elbowed"] = False
        ar["roughness"] = 1
        ar["roundness"] = {"type": 3}
        errs = diagram.validate_doc(doc)
        self.assertTrue(any("elbowed" in e for e in errs), errs)
        self.assertTrue(any("roughness" in e for e in errs), errs)
        self.assertTrue(any("roundness" in e for e in errs), errs)

    def test_bat_palette_la(self):
        doc, _, _, _, _ = good_doc()
        shape = next(el for el in doc["elements"] if el["type"] == "rectangle")
        shape["backgroundColor"] = "#123456"
        self.assertTrue(any("palette" in e for e in diagram.validate_doc(doc)))

    def test_cli_exit_1_khi_loi(self):
        doc, _, _, _, _ = good_doc()
        shape = next(el for el in doc["elements"] if el["type"] == "rectangle")
        shape["type"] = "diamond"
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "x.excalidraw")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "diagram.py"),
                            "validate", p], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1)
        self.assertIn("diamond", r.stdout)


class TestMutation(unittest.TestCase):
    """Tu mutation: hoan tac tung logic loi tren ban sao tam, test phai bat duoc."""

    def test_mutation_bo_cam_diamond(self):
        mut = load_mutant('if el.get("type") == "diamond":',
                          'if el.get("type") == "diamond-nope":')
        doc, _, _, _, _ = good_doc()
        shape = next(el for el in doc["elements"] if el["type"] == "rectangle")
        shape["type"] = "diamond"
        self.assertEqual([e for e in mut.validate_doc(doc) if "diamond" in e], [])
        self.assertNotEqual([e for e in diagram.validate_doc(doc) if "diamond" in e], [])

    def test_mutation_bo_kiem_binding(self):
        mut = load_mutant('errs.append("text \'%s\' thieu containerId" % el.get("id"))',
                          'errs.append("text \'%s\' van on" % el.get("id"))')
        doc, _, _, _, _ = good_doc()
        txt = next(el for el in doc["elements"] if el["type"] == "text")
        txt.pop("containerId")
        self.assertEqual([e for e in mut.validate_doc(doc) if "thieu containerId" in e], [])
        self.assertNotEqual([e for e in diagram.validate_doc(doc) if "thieu containerId" in e], [])

    def test_mutation_bo_kiem_mep_mui_ten(self):
        mut = load_mutant('> TOL + 1e-9:',
                          '> 1e18:')
        doc, _, _, _, _ = good_doc()
        ar = next(el for el in doc["elements"] if el["type"] == "arrow")
        ar["points"][1][0] += 10
        self.assertEqual([e for e in mut.validate_doc(doc) if "lech mep" in e], [])
        self.assertNotEqual([e for e in diagram.validate_doc(doc) if "lech mep" in e], [])

    def test_mutation_bo_text_elements_obsidian(self):
        mut = load_mutant('"# Text Elements"',
                          '"# Nope Elements"')
        doc, spec, pos, W, H = good_doc()
        md = mut.build_obsidian_md(spec, doc)
        self.assertNotIn("# Text Elements", md)  # mutant mat bang...
        self.assertIn("# Text Elements", diagram.build_obsidian_md(spec, doc))  # ...code that con


if __name__ == "__main__":
    unittest.main()
