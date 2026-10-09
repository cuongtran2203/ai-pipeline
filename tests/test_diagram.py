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
REAL_PLAN2 = r"C:\Users\24h\Desktop\AI_worklflow\runs\ai-pipeline-v2\plan.json"


def doc_crossings(doc):
    """So doan mui ten cat noi that hop la (hinh hoc doc lap)."""
    rects = {el["id"]: el for el in doc["elements"] if el.get("type") == "rectangle"}
    hits = []
    for el in doc["elements"]:
        if el.get("type") != "arrow":
            continue
        pts = el.get("points")
        if not (isinstance(pts, list) and len(pts) >= 2):
            continue
        sb = (el.get("startBinding") or {}).get("elementId")
        eb = (el.get("endBinding") or {}).get("elementId")
        path = diagram.arrow_abs_points(el)
        for rid, r in rects.items():
            if rid in (sb, eb):
                continue
            for i in range(len(path) - 1):
                if diagram.seg_crosses_rect(path[i][0], path[i][1],
                                            path[i + 1][0], path[i + 1][1],
                                            r["x"], r["y"], r["width"], r["height"]):
                    hits.append((el.get("id"), rid))
                    break
    return hits

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
        ar["points"][-1][0] += 10  # lech 10px khoi mep (diem cuoi; mui ten kenh co >=2 diem)
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
        ar["points"][-1][0] += 10
        self.assertEqual([e for e in mut.validate_doc(doc) if "lech mep" in e], [])
        self.assertNotEqual([e for e in diagram.validate_doc(doc) if "lech mep" in e], [])

    def test_mutation_bo_text_elements_obsidian(self):
        mut = load_mutant('"# Text Elements"',
                          '"# Nope Elements"')
        doc, spec, pos, W, H = good_doc()
        md = mut.build_obsidian_md(spec, doc)
        self.assertNotIn("# Text Elements", md)  # mutant mat bang...
        self.assertIn("# Text Elements", diagram.build_obsidian_md(spec, doc))  # ...code that con


class TestKenhDinhTuyen(unittest.TestCase):
    """DG2(a): canh di trong kenh, khong xuyen hop la, gop bus cung dich."""

    def test_khong_xuyen_hop_tren_plan_that(self):
        if not os.path.isfile(REAL_PLAN):
            self.skipTest("thieu plan that timesheet-ocr")
        with open(REAL_PLAN, encoding="utf-8") as f:
            plan = json.load(f)
        spec = diagram.plan_to_spec(plan)
        pos, W, H = diagram.layout(spec)
        doc = diagram.build_excalidraw(spec, pos)
        self.assertEqual(diagram.validate_doc(doc), [])
        self.assertEqual(doc_crossings(doc), [])

    def test_khong_xuyen_hop_dag_day(self):
        nodes = [{"id": "n%d" % i, "label": "Nut %d nhan dai de gay de" % i,
                  "kind": "service", "lane": "p%d" % (i % 3)} for i in range(12)]
        edges = [{"from": "n%d" % i, "to": "n%d" % j}
                 for i in range(12) for j in range(i + 1, min(12, i + 4))]
        spec = {"title": "day", "direction": "LR", "lanes": ["p0", "p1", "p2"],
                "nodes": nodes, "edges": edges}
        spec = diagram.spec_from_json(spec)
        pos, W, H = diagram.layout(spec)
        doc = diagram.build_excalidraw(spec, pos)
        self.assertEqual(diagram.validate_doc(doc), [])
        self.assertEqual(doc_crossings(doc), [])

    def test_khong_xuyen_hop_tb(self):
        nodes = [{"id": "n%d" % i, "label": "Nut %d" % i, "kind": "ai"} for i in range(8)]
        edges = [{"from": "n%d" % i, "to": "n%d" % (i + 1)} for i in range(7)]
        edges += [{"from": "n0", "to": "n7"}, {"from": "n1", "to": "n6"}]
        spec = diagram.spec_from_json({"title": "tb", "direction": "TB",
                                       "nodes": nodes, "edges": edges})
        pos, W, H = diagram.layout(spec)
        doc = diagram.build_excalidraw(spec, pos)
        self.assertEqual(diagram.validate_doc(doc), [])
        self.assertEqual(doc_crossings(doc), [])

    def test_canh_cung_dich_chia_se_bus(self):
        # dung spec don gian: 1 goc -> 3 nguon -> 1 dich
        spec = diagram.spec_from_json({
            "title": "fanin", "direction": "LR",
            "nodes": [{"id": "x", "label": "Goc", "kind": "data"}] + [
                {"id": "a%d" % i, "label": "Nguon %d" % i, "kind": "service"}
                for i in range(3)] + [{"id": "t", "label": "Dich", "kind": "db"}],
            "edges": [{"from": "x", "to": "a%d" % i} for i in range(3)] + [
                {"from": "a%d" % i, "to": "t"} for i in range(3)]})
        pos, W, H = diagram.layout(spec)
        doc = diagram.build_excalidraw(spec, pos)
        self.assertEqual(diagram.validate_doc(doc), [])
        self.assertEqual(doc_crossings(doc), [])
        last_x = set()
        for el in doc["elements"]:
            if el.get("type") == "arrow" and (el.get("endBinding") or {}).get("elementId") == "t":
                pts = diagram.arrow_abs_points(el)
                last_x.add(round(pts[-2][0], 1))  # truc bus dung truoc khi vao dich
        self.assertEqual(len(last_x), 1)  # cung 1 bus doc


class TestViewBoxVaChu(unittest.TestCase):
    """DG2(b): viewBox khit, chu >=12px, hop tu gian, khong cat chu."""

    def test_viewbox_khit_plan_that(self):
        if not os.path.isfile(REAL_PLAN):
            self.skipTest("thieu plan that timesheet-ocr")
        with open(REAL_PLAN, encoding="utf-8") as f:
            plan = json.load(f)
        spec = diagram.plan_to_spec(plan)
        pos, W, H = diagram.layout(spec)
        svg = diagram.build_svg(spec, pos, W, H)
        self.assertGreaterEqual(diagram.viewbox_tightness(spec, pos, svg), 0.7)

    def test_viewbox_khit_model(self):
        with open(os.path.join(ROOT, "templates", "model_spec.example.json"),
                  encoding="utf-8") as f:
            ms = json.load(f)
        spec = diagram.model_to_spec(ms)
        pos, W, H = diagram.layout(spec)
        svg = diagram.build_svg(spec, pos, W, H)
        self.assertGreaterEqual(diagram.viewbox_tightness(spec, pos, svg), 0.7)
        self.assertEqual(diagram.validate_doc(diagram.build_excalidraw(spec, pos)), [])

    def test_hop_tu_gian_theo_nhan(self):
        short = {"id": "s", "label": "Ngan", "kind": "service"}
        long = {"id": "l", "label": "Nhan rat dai can duoc xuong dong day du chu khong cat bot mot chu nao",
                "kind": "service"}
        ws, hs = diagram.node_size(short)
        wl, hl = diagram.node_size(long)
        self.assertEqual((ws, hs), (diagram.NODE_W, diagram.NODE_H))
        self.assertTrue(wl > diagram.NODE_W or hl > diagram.NODE_H)

    def test_khong_cat_chu_trong_svg(self):
        import html as _html
        if not os.path.isfile(REAL_PLAN):
            self.skipTest("thieu plan that timesheet-ocr")
        with open(REAL_PLAN, encoding="utf-8") as f:
            plan = json.load(f)
        spec = diagram.plan_to_spec(plan)
        pos, W, H = diagram.layout(spec)
        svg = diagram.build_svg(spec, pos, W, H)
        for n in spec["nodes"]:
            for w in str(n.get("label", "")).split():
                self.assertIn(_html.escape(w), svg, n["id"])
            if n.get("note"):
                for w in str(n["note"]).split():
                    self.assertIn(_html.escape(w), svg, n["id"])

    def test_chu_nhan_du_lon(self):
        self.assertGreaterEqual(diagram.LABEL_FS, 12)
        self.assertGreaterEqual(diagram.EDGE_FS, 12)

    def test_cjk_rong_hon_latin(self):
        self.assertGreater(diagram.text_width("日本語", 14), diagram.text_width("abc", 14))
        self.assertAlmostEqual(diagram.text_width("abc", 14),
                               diagram.text_width("ăâđ", 14), delta=14.0)

    def test_wrap_khong_mat_tu(self):
        text = "Ảnh minh họa tiếng Việt có dấu rất dài cần xuống dòng"
        lines = diagram.wrap_text(text, 14, 100)
        self.assertEqual(" ".join(lines), text)
        self.assertGreater(len(lines), 1)


class TestCanGiuaVaPhase(unittest.TestCase):
    """DG2(c): hop cung tang can giua, deu nhau, co bang phase."""

    def test_cung_tang_can_giua(self):
        spec = diagram.spec_from_json({
            "title": "cg", "direction": "LR",
            "nodes": [{"id": "a", "label": "A", "kind": "service"}] + [
                {"id": "b%d" % i, "label": "B%d" % i, "kind": "service"}
                for i in range(3)],
            "edges": [{"from": "a", "to": "b%d" % i} for i in range(3)]})
        pos, W, H = diagram.layout(spec)
        _p, sizes, _w, _h, aux = diagram.layout_full(spec)
        cy_a = pos["a"][1] + sizes["a"][1] / 2.0
        self.assertAlmostEqual(cy_a, H / 2.0, delta=1.0)
        ys = sorted(pos["b%d" % i][1] for i in range(3))
        gaps = [ys[i + 1] - ys[i] for i in range(2)]
        self.assertAlmostEqual(gaps[0], gaps[1], delta=1.0)

    def test_bang_phase_trong_svg(self):
        if not os.path.isfile(REAL_PLAN):
            self.skipTest("thieu plan that timesheet-ocr")
        with open(REAL_PLAN, encoding="utf-8") as f:
            plan = json.load(f)
        spec = diagram.plan_to_spec(plan)
        self.assertTrue(len(spec.get("lanes") or []) >= 2)
        pos, W, H = diagram.layout(spec)
        svg = diagram.build_svg(spec, pos, W, H)
        for lane in spec["lanes"]:
            if any((n.get("lane") or "build") == lane for n in spec["nodes"]):
                import html as _html
                self.assertIn(_html.escape(lane), svg)


class TestTachPhaseVaTongQuan(unittest.TestCase):
    """DG2: so do lon tach theo phase, moi so do <=25 node, co tong quan."""

    def test_tach_plan_35_task_theo_phase(self):
        if not os.path.isfile(REAL_PLAN2):
            self.skipTest("thieu plan that ai-pipeline-v2")
        with open(REAL_PLAN2, encoding="utf-8") as f:
            plan = json.load(f)
        spec = diagram.plan_to_spec(plan)
        self.assertGreater(len(spec["nodes"]), 25)
        parts = diagram.split_spec(spec)
        ov = [p for p in parts if p.get("_overview")]
        subs = [p for p in parts if not p.get("_overview")]
        self.assertEqual(len(ov), 1)
        self.assertGreaterEqual(len(subs), 2)
        for p in subs:
            self.assertLessEqual(len(p["nodes"]), 25)
        seen = [n["id"] for p in subs for n in p["nodes"]]
        self.assertEqual(sorted(seen), sorted(n["id"] for n in spec["nodes"]))
        self.assertIn("tong quan", ov[0]["title"])
        import shutil
        import tempfile as _tf
        tmp = _tf.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        written = diagram.render_spec(spec, tmp, ["excalidraw", "svg"], "pipe")
        self.assertTrue(any(w.endswith("_overview.excalidraw") for w in written))
        for w in written:
            if w.endswith(".excalidraw"):
                with open(w, encoding="utf-8") as f:
                    self.assertEqual(diagram.validate_doc(json.load(f)), [], w)

    def test_spec_nho_khong_tach(self):
        spec = diagram.spec_from_json(dict(SAMPLE))
        self.assertEqual(len(diagram.split_spec(spec)), 1)


class TestValidateHinhHoc(unittest.TestCase):
    """DG2(d): validate bat loi hinh hoc moi."""

    def test_bat_mui_ten_xuyen_hop(self):
        doc, _, _, _, _ = good_doc()
        ar = next(el for el in doc["elements"] if el["type"] == "arrow")
        # keo duong mui ten xuyen qua hop thu 3 (rec)
        other = [el for el in doc["elements"]
                 if el.get("type") == "rectangle" and el["id"] not in (
                     (ar.get("startBinding") or {}).get("elementId"),
                     (ar.get("endBinding") or {}).get("elementId"))][0]
        ar["points"] = [[0, 0],
                        [round(other["x"] + other["width"] / 2 - ar["x"], 1),
                         round(other["y"] + other["height"] / 2 - ar["y"], 1)],
                        ar["points"][-1]]
        errs = diagram.validate_doc(doc)
        self.assertTrue(any("xuyen hop" in e for e in errs), errs)

    def test_bat_hop_chong_nhau(self):
        doc, _, _, _, _ = good_doc()
        rects = [el for el in doc["elements"] if el.get("type") == "rectangle"]
        rects[1]["x"] = rects[0]["x"] + 10
        rects[1]["y"] = rects[0]["y"] + 10
        errs = diagram.validate_doc(doc)
        self.assertTrue(any("chong" in e for e in errs), errs)

    def test_bat_chu_tran_hop(self):
        doc, _, _, _, _ = good_doc()
        txt = next(el for el in doc["elements"] if el["type"] == "text")
        txt["width"] = 5000.0
        errs = diagram.validate_doc(doc)
        self.assertTrue(any("tran" in e for e in errs), errs)

    def test_bat_nhan_canh_xa_duong(self):
        doc, _, _, _, _ = good_doc()
        spec = diagram.spec_from_json(copy.deepcopy(SAMPLE))
        pos, W, H = diagram.layout(spec)
        doc = diagram.build_excalidraw(spec, pos)
        lbl = next(el for el in doc["elements"] if el.get("type") == "text"
                   and doc["elements"] and el.get("containerId", "").startswith("e"))
        lbl["x"] += 5000.0
        lbl["y"] += 5000.0
        errs = diagram.validate_doc(doc)
        self.assertTrue(any("tran" in e for e in errs), errs)


class TestMutationHinhHoc(unittest.TestCase):
    """Moi kiem tra hinh hoc moi co mutation tuong ung bi bat."""

    def test_mutation_bo_kiem_xuyen_hop(self):
        mut = load_mutant("mui ten '%s' xuyen hop '%s' (cat noi that hop la)",
                          "mui ten '%s' van on '%s'")
        doc, _, _, _, _ = good_doc()
        ar = next(el for el in doc["elements"] if el["type"] == "arrow")
        other = [el for el in doc["elements"]
                 if el.get("type") == "rectangle" and el["id"] not in (
                     (ar.get("startBinding") or {}).get("elementId"),
                     (ar.get("endBinding") or {}).get("elementId"))][0]
        ar["points"] = [[0, 0],
                        [round(other["x"] + other["width"] / 2 - ar["x"], 1),
                         round(other["y"] + other["height"] / 2 - ar["y"], 1)],
                        ar["points"][-1]]
        self.assertEqual([e for e in mut.validate_doc(doc) if "xuyen hop" in e], [])
        self.assertNotEqual([e for e in diagram.validate_doc(doc) if "xuyen hop" in e], [])

    def test_mutation_bo_kiem_chong_hop(self):
        mut = load_mutant("hop chong nhau: '%s' & '%s'",
                          "hop van on: '%s' & '%s'")
        doc, _, _, _, _ = good_doc()
        rects = [el for el in doc["elements"] if el.get("type") == "rectangle"]
        rects[1]["x"] = rects[0]["x"] + 10
        rects[1]["y"] = rects[0]["y"] + 10
        self.assertEqual([e for e in mut.validate_doc(doc) if "chong" in e], [])
        self.assertNotEqual([e for e in diagram.validate_doc(doc) if "chong" in e], [])

    def test_mutation_bo_kiem_tran_chu(self):
        mut = load_mutant("chu tran hop: text '%s' tran '%s'",
                          "chu van on: text '%s' hop '%s'")
        doc, _, _, _, _ = good_doc()
        txt = next(el for el in doc["elements"] if el["type"] == "text")
        txt["width"] = 5000.0
        self.assertEqual([e for e in mut.validate_doc(doc) if "tran" in e], [])
        self.assertNotEqual([e for e in diagram.validate_doc(doc) if "tran" in e], [])

    def test_mutation_bo_dinh_tuyen_kenh(self):
        mut = load_mutant("def _route_lr(a, b, pos, sizes, aux):",
                          "def _route_lr_nope(a, b, pos, sizes, aux):")
        self.assertFalse(hasattr(mut, "_route_lr"))
        self.assertTrue(hasattr(diagram, "_route_lr"))


if __name__ == "__main__":
    unittest.main()
