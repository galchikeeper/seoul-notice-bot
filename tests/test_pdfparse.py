"""실제 공고문 PDF로 표 읽기를 시험한다.  실행: python -m unittest discover tests"""
import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pdfparse as P

FX = pathlib.Path(__file__).parent / "fixtures"


def load(bid):
    rows, why = P.parse_pdf_bytes((FX / f"soco_{bid}.pdf").read_bytes())
    return rows, why


def prices(row):
    return [(p["ratio"], p["deposit"], p["rent"]) for p in row["prices"]]


class T(unittest.TestCase):
    def test_6732_manwon_two_types(self):          # 공덕역 e·seo — 만원 단위, 비율 3개
        rows, why = load(6732)
        self.assertEqual(why, "")
        self.assertEqual([(r["label"], r["area"]) for r in rows], [("37 TYPE", "37.36"), ("42 TYPE", "42.57")])
        self.assertEqual(prices(rows[0]), [("30%", 12820, 109), ("35%", 14960, 102), ("40%", 17100, 94)])

    def test_6730_won_unit(self):                   # 청계로벤하임 — 원 단위 → 만원, 소수 유지
        rows, _ = load(6730)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["area"], "33")
        self.assertEqual(prices(rows[0]), [("기준", 20170, 25.32)])

    def test_6731_three_types_ratios(self):         # 상봉생활 — 30/50/70%
        rows, _ = load(6731)
        self.assertEqual([r["label"] for r in rows], ["25형", "54형", "84A형"])
        self.assertEqual(prices(rows[2]), [("30%", 16230, 132), ("50%", 27050, 94), ("70%", 37870, 56)])

    def test_6704_won_unit_ratios(self):            # 상봉동양엔파트 — 원 단위 + 비율 50/60/80%
        rows, _ = load(6704)
        self.assertEqual(prices(rows[0]), [("50%", 13200, 54.45), ("60%", 15840, 44.55), ("80%", 21100, 24.82)])

    def test_6718_two_tables_special_general(self): # 표가 특별·일반으로 나뉜 공고
        rows, _ = load(6718)
        self.assertEqual(len(rows), 8)
        self.assertIn("특별공급", rows[0]["kind"])
        self.assertIn("일반공급", rows[4]["kind"])

    def test_no_small_tenant_sentence(self):         # 예전 오류값(소액임차인 문장)이 다시 나오지 않는다
        for bid in (6730, 6731, 6732, 6704, 6718):
            rows, _ = load(bid)
            for r in rows:
                self.assertNotEqual(r["area"], "23.02")

    def test_unit_ambiguous_fails(self):            # 단위가 애매하면 채우지 않고 실패
        t = [["공급 유형", "주거 전용 (타입)", "보증금", "임대료"], ["청년", "20.00㎡ (25A)", "500,000", "3,000"]]
        rows, why = P.parse_table(t)
        self.assertEqual(rows, [])
        self.assertIn("단위", why)

    def test_lines_fail_text(self):
        self.assertIn("확인 실패", P.type_lines([]))


if __name__ == "__main__":
    unittest.main()
