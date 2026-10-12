#!/usr/bin/env python3
"""Hoi quy scripts/tech_report.py: init sinh khung, check bat placeholder/HINT/muc thieu/bang chung/round thieu."""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import tech_report  # noqa: E402
import project_status  # noqa: E402


def fill(path):
    """Dien moi placeholder va xoa HINT; dat Phu luc D hop le."""
    t = open(path, encoding="utf-8").read()
    t = re.sub(r"<!--\s*HINT.*?-->", "", t, flags=re.S)
    t = re.sub(r"\{\{CLAIM_1\}\}", "ROUGE-L tang len 0,91", t)
    t = re.sub(r"\{\{EVIDENCE_1\}\}", "Bang 2", t)
    t = re.sub(r"\{\{STATUS_1\}\}", "Đã có bằng chứng", t)
    t = re.sub(r"\{\{[A-Z0-9_]+\}\}", "N/A", t)
    open(path, "w", encoding="utf-8").write(t)


class TestTechReport(unittest.TestCase):
    def test_init_dien_khoa_khong_ghi_de(self):
        with tempfile.TemporaryDirectory() as d:
            p, wrote = tech_report.init(d, {"TITLE": "Bai toan X"})
            self.assertTrue(wrote)
            text = open(p, encoding="utf-8").read()
            self.assertIn("# Bai toan X", text)
            self.assertIn("{{ABSTRACT}}", text)
            open(p, "w", encoding="utf-8").write("da viet")
            _, wrote2 = tech_report.init(d, {})
            self.assertFalse(wrote2)
            self.assertEqual(open(p, encoding="utf-8").read(), "da viet")

    def test_check_thieu_file_va_khung_chua_dien(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(any("thieu" in x for x in tech_report.check(d)))
            tech_report.init(d, {})
            probs = tech_report.check(d)
            self.assertTrue(any("placeholder" in x for x in probs))
            self.assertTrue(any("HINT" in x for x in probs))

    def test_check_dat_khi_dien_du(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = tech_report.init(d, {})
            fill(p)
            self.assertEqual(tech_report.check(d), [])

    def test_check_bat_muc_thieu_va_trang_thai_sai(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = tech_report.init(d, {})
            fill(p)
            t = open(p, encoding="utf-8").read()
            open(p, "w", encoding="utf-8").write(t.replace("## 8. Thực nghiệm bổ sung", "## 8. Khac"))
            self.assertTrue(any("kết quả âm" in x for x in tech_report.check(d)))
            open(p, "w", encoding="utf-8").write(t.replace("| Đã có bằng chứng |", "| chac dung |"))
            self.assertTrue(any("trang thai" in x for x in tech_report.check(d)))

    def test_check_so_lieu_tom_tat_phai_co_trong_than_bai(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = tech_report.init(d, {"ABSTRACT": "Dat 0,9125 tren dev."})
            fill(p)
            self.assertTrue(any("0.9125" in x for x in tech_report.check(d)))
            t = open(p, encoding="utf-8").read()
            open(p, "w", encoding="utf-8").write(t.replace("## 5. Kết quả thực nghiệm (Results)", "## 5. Kết quả thực nghiệm (Results)\n\nROUGE-L 0.9125."))
            self.assertEqual(tech_report.check(d), [])

    def test_check_bat_round_thieu_report(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = tech_report.init(d, {})
            fill(p)
            r = os.path.join(d, "reports", "round-01-x")
            os.makedirs(r)
            open(os.path.join(r, "report.md"), "w").write("x")
            self.assertTrue(any("round-01-x: thieu report.html" in x for x in tech_report.check(d)))

    def test_project_status_nhac_khi_chua_co(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(project_status.tech_report_gap(d))
            p, _ = tech_report.init(d, {})
            fill(p)
            self.assertEqual(project_status.tech_report_gap(d), [])


if __name__ == "__main__":
    unittest.main()
