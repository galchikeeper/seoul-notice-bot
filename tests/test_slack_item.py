"""슬랙 공고 메시지: 면적 기준(30㎡)과 '확인 실패' 처리.  실행: python -m unittest discover tests"""
import datetime as dt, pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import bot

TODAY = dt.date(2026, 11, 18)


def t(area, label="25형", kind="청년 일반", dep=6000, rent=57):
    return {"kind": kind, "label": label, "area": area, "prices": [{"ratio": "30%", "deposit": dep, "rent": rent}]}


def n(types, pdf="x.pdf", deadline="11/28"):
    return {"title": "[민간임대] 시험역 시험단지 추가모집공고", "name": "시험단지", "deadline": deadline,
            "url": "https://example.invalid/n", "pdf": pdf, "types": types}


class T(unittest.TestCase):
    def test_drop_small_types(self):
        c = bot.slack_item(n([t("20.20"), t("43.61", "54형")]), TODAY, fetch_pdf=False)
        self.assertFalse(c["skip"])
        self.assertEqual([r[0] for r in c["rows"]], ["54형"])
        self.assertEqual(c["flags"], [])

    def test_all_small_skip(self):
        c = bot.slack_item(n([t("17.11"), t("29.99")]), TODAY, fetch_pdf=False)
        self.assertTrue(c["skip"])

    def test_unknown_area_flag(self):
        c = bot.slack_item(n([t(""), t("17.11")]), TODAY, fetch_pdf=False)
        self.assertFalse(c["skip"])
        self.assertEqual(c["flags"], ["📐 면적 확인 필요"])
        self.assertEqual(c["rows"][0][1], "확인 실패")

    def test_pdf_fail_not_filled(self):
        c = bot.slack_item(n([], pdf="x.pdf"), TODAY, fetch_pdf=False)
        self.assertEqual(c["rows"], [["확인 실패"] * 6])
        self.assertIn("📐 면적 확인 필요", c["flags"])

    def test_urgent_top_line(self):
        c = bot.slack_item(n([t("43.61")], deadline="11/19(목) 18:00"), TODAY, fetch_pdf=False)
        self.assertEqual(c["top"], "⚠️ D-1 · 시험단지 · 마감 11/19(목) 18:00")


if __name__ == "__main__":
    unittest.main()
