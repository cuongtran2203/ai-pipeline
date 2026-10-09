#!/usr/bin/env python3
"""Hoi quy scripts/render_report.py: truong diagrams tuy chon + hanh vi cu doi."""
import copy
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import render_report  # noqa: E402

SAMPLE = os.path.join(ROOT, "examples", "eval.sample.json")

BASE = {
    "title": "Bao cao mau",
    "version": {"model": "m-v1", "dataset": "ds-v1"},
    "overview": {"status": "s", "method": "m", "result": "r"},
    "tables": [{"name": "T", "rows": [{"item": "acc", "value": 0.9}]}],
    "errors": [],
    "conclusion": {"fixes": [{"priority": "P1", "error": "e", "fix": "f", "measure": "kpi"}]},
}


class TestDiagrams(unittest.TestCase):
    def test_thieu_diagrams_hanh_vi_cu_khong_doi(self):
        d = copy.deepcopy(BASE)
        md, html = render_report.render_md(d), render_report.render_html(d)
        self.assertNotIn("Sơ đồ", md)
        self.assertNotIn("<figure>", html)
        # So voi mau cu: khong co muc diagrams trong ca 2 ngon ngu.
        self.assertNotIn("Diagrams", render_report.render_md(dict(d, lang="en")))

    def test_md_chen_lien_ket_svg(self):
        d = copy.deepcopy(BASE)
        d["diagrams"] = [{"title": "Pipeline", "svg": "diagrams/pipe.svg",
                          "excalidraw": "diagrams/pipe.excalidraw"}]
        md = render_report.render_md(d)
        self.assertIn("### Sơ đồ", md)
        self.assertIn("[Pipeline](diagrams/pipe.svg)", md)
        self.assertIn("diagrams/pipe.excalidraw", md)

    def test_html_nhung_svg_noi_tuyen(self):
        import tempfile as tf
        tmp = tf.mkdtemp()
        svg_path = os.path.join(tmp, "a.svg")
        with open(svg_path, "w", encoding="utf-8", newline="\n") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg"><text>Ảnh phở</text></svg>')
        d = copy.deepcopy(BASE)
        d["diagrams"] = [{"title": "Kiến trúc", "svg": svg_path}]
        html = render_report.render_html(d)
        self.assertIn("<figure>", html)
        self.assertIn("Ảnh phở", html)  # tieng Viet nguyen ven
        self.assertIn("<figcaption>Kiến trúc</figcaption>", html)

    def test_html_thieu_file_svg_giu_chu(self):
        d = copy.deepcopy(BASE)
        d["diagrams"] = [{"title": "Mat file", "svg": "/khong/ton/tai/a.svg"}]
        html = render_report.render_html(d)
        self.assertIn("Mat file", html)
        self.assertNotIn("<figure>", html)

    def test_eval_mau_cu_van_render(self):
        d = json.load(open(SAMPLE, encoding="utf-8"))
        md, html = render_report.render_md(d), render_report.render_html(d)
        self.assertIn("Tổng quan", md)
        self.assertIn(d["title"][:10], html)


if __name__ == "__main__":
    unittest.main()
