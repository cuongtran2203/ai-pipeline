#!/usr/bin/env python3
"""Hoi quy scripts/round_docs.py: init sinh du 3 tai lieu, check bat placeholder va file thieu."""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import round_docs  # noqa: E402


class TestRoundDocs(unittest.TestCase):
    def test_init_sinh_3_file_va_dien_khoa(self):
        with tempfile.TemporaryDirectory() as d:
            written, skipped = round_docs.init(d, {"ROUND_TITLE": "round-01 demo", "VERSION": "v1"})
            self.assertEqual(sorted(written), sorted(round_docs.DOCS))
            self.assertEqual(skipped, [])
            text = open(os.path.join(d, "results_report.html"), encoding="utf-8").read()
            self.assertIn("round-01 demo", text)
            self.assertNotIn("{{ROUND_TITLE}}", text)
            self.assertIn("{{ACHIEVEMENT_1}}", text)  # khoa chua cung cap van la placeholder

    def test_check_bat_placeholder_roi_dat_khi_da_dien(self):
        with tempfile.TemporaryDirectory() as d:
            round_docs.init(d, {})
            self.assertTrue(any("placeholder" in p for p in round_docs.check(d)))
            for name in round_docs.DOCS:
                p = os.path.join(d, name)
                t = open(p, encoding="utf-8").read()
                open(p, "w", encoding="utf-8").write(re.sub(r"\{\{[A-Z0-9_]+\}\}", "N/A", t))
            self.assertEqual(round_docs.check(d), [])

    def test_check_bat_file_thieu(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(len(round_docs.check(d)), 3)

    def test_init_khong_ghi_de_file_da_dien(self):
        with tempfile.TemporaryDirectory() as d:
            round_docs.init(d, {})
            p = os.path.join(d, "data_report.html")
            open(p, "w", encoding="utf-8").write("da dien")
            written, skipped = round_docs.init(d, {})
            self.assertIn("data_report.html", skipped)
            self.assertEqual(open(p, encoding="utf-8").read(), "da dien")


if __name__ == "__main__":
    unittest.main()
