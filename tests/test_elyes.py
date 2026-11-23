"""롯데 엘리스: 제목·공고문 이미지에서 마감·임대조건 읽기, 마감 지난 공고 거르기.  실행: python -m unittest discover tests"""
import datetime as dt, pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import bot, elyes_ocr as OCR, slack as SL, sources as S

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
D = dt.datetime


def notice(title, posted="2026.11.20"):
    return {"board_id": "elyes:x", "source": "elyes", "title": f"롯데 엘리스 — {title}", "name": title[:40],
            "raw_title": title, "url": S.elyes_detail_url("ab+c=="), "pdf": None, "images": [], "posted": posted}


class TitleDeadline(unittest.TestCase):
    def test_formats(self):
        cases = {
            "[어바니엘 천호] 공실세대 모집공고 (접수기간: 11/20~11/22)": (dt.date(2026, 11, 22), None),
            "[사송 롯데캐슬] 재임대 모집공고(접수기간: 26.11.11~26.11.15)": (dt.date(2026, 11, 15), None),
            "[독산역 롯데캐슬] 재임대 모집공고(접수: '26.11.13(금) 10~15시)": (dt.date(2026, 11, 13), 15),
            "[용산원효루미니] 추가모집공고(접수일 '26.11.17)": (dt.date(2026, 11, 17), None),
            "[남영역 롯데캐슬] 공실세대 모집공고 (공고일:'26.11.20)": None,      # 공고일은 마감이 아니다
        }
        for t, want in cases.items():
            self.assertEqual(S.elyes_title_deadline(t, "2026.11.20"), want, t)

    def test_year_wrap(self):
        self.assertEqual(S.elyes_title_deadline("[x] 모집공고 (접수기간: 12/30~1/2)", "2026.12.29"),
                         (dt.date(2027, 1, 2), None))

    def test_detail_url(self):
        self.assertEqual(S.elyes_detail_url("HQf+P2==", "BHCT"),
                         "https://www.elyes.co.kr/post/recruit/detail?i_sNtCode=BHCT&nt_idx=HQf%2BP2%3D%3D")


class Parse(unittest.TestCase):
    def test_when(self):
        self.assertEqual(OCR.parse_when("2026.11.20( 금 ) 18 시\n~ 2026.11.22( 일 ) 18 시"),
                         (D(2026, 11, 20, 18), D(2026, 11, 22, 18)))
        self.assertEqual(OCR.parse_when("2026.11.23( 월 )\n09 시 ~ 18 시 까 지"),
                         (D(2026, 11, 23, 9), D(2026, 11, 23, 18)))
        self.assertEqual(OCR.parse_when("26.11.20( 금 ) 18 시 ~ 26.11.22( 일 ) 18 시")[1], D(2026, 11, 22, 18))
        self.assertEqual(OCR.parse_when("2026.11.23(월) ~ 2026.11.27(금)")[1], D(2026, 11, 27, 23, 59))
        self.assertEqual(OCR.parse_when("공고문 확인"), (None, None))

    def test_money(self):
        self.assertEqual(OCR.money("10,000,000"), 10_000_000)
        self.assertEqual(OCR.money("1,500 만"), 15_000_000)
        self.assertEqual(OCR.money("1.500만"), 15_000_000)               # GitHub(우분투) tesseract 가 읽은 그대로
        self.assertEqual(OCR.money("10.000.000"), 10_000_000)
        self.assertEqual(OCR.money("1억 9,900만"), 199_000_000)
        self.assertIsNone(OCR.money("주의사항 필독"))

    def test_plausible(self):
        self.assertTrue(OCR.plausible({"deposit": 10_000_000, "rent": 628_000}))
        self.assertFalse(OCR.plausible({"deposit": 15008, "rent": 690_000}))      # 자릿수를 잘못 읽은 값


class Enrich(unittest.TestCase):
    def test_expired_not_sent(self):
        n = notice("[어바니엘 천호] 공실세대 모집공고 (접수기간: 11/20~11/22)")
        self.assertIsNone(bot.elyes_enrich(n, D(2026, 11, 23, 7, 47), images=[]))

    def test_open_until_end_of_day(self):        # 제목에 시각이 없으면 그날 23:59 까지 열린 것으로 본다
        n = notice("[어바니엘 천호] 공실세대 모집공고 (접수기간: 11/20~11/22)")
        e = bot.elyes_enrich(n, D(2026, 11, 22, 20, 0), images=[])
        self.assertEqual(e["deadline"], "11/22(일)")
        c = bot.slack_item(e, dt.date(2026, 11, 22))
        self.assertIn("D-DAY", c["top"])
        self.assertIsNone(c["rows"])
        self.assertIn("공고문 확인", c["fallback_text"])
        self.assertTrue(c["notice_url"].startswith("https://www.elyes.co.kr/post/recruit/detail?"))

    def test_unknown_deadline_flag(self):
        n = notice("[남영역 롯데캐슬] 공실세대 모집공고 (공고일:'26.11.20)")
        e = bot.elyes_enrich(n, D(2026, 11, 23, 7, 47), images=[])
        c = bot.slack_item(e, dt.date(2026, 11, 23))
        self.assertIn("⏰ 마감일 확인 필요", c["flags"])
        self.assertIn("마감 확인 필요", c["top"])
        self.assertEqual(c["top"].split(" · ")[0], "남영역 롯데캐슬 공실세대 모집공고")

    def test_area_rule(self):
        rows = [{"type": "A2", "deposit": 15_000_000, "rent": 698_000, "area": "14.98"},
                {"type": "B", "deposit": 20_000_000, "rent": 870_000, "area": "34.10"}]
        n = {**notice("[x] 모집공고 (접수기간: 11/20~11/22)"), "ely_rows": rows, "deadline": "11/22(일)"}
        c = bot.slack_item(n, dt.date(2026, 11, 21))
        self.assertEqual([r[0] for r in c["rows"]], ["B"])
        self.assertNotIn("📐 면적 확인 필요", c["flags"])
        self.assertTrue(bot.slack_item({**n, "ely_rows": rows[:1]}, dt.date(2026, 11, 21))["skip"])

    def test_table_blocks(self):
        rows = [{"type": "A", "deposit": 10_000_000, "rent": 628_000, "floor": "10층", "dir": "남향", "use": "도생"}]
        n = {**notice("[어바니엘 천호] 공실세대 모집공고 (접수기간: 11/20~11/22)"), "ely_rows": rows,
             "deadline": "11/22(일) 18:00", "days_left": 1, "dday": "D-1",
             "ely_sched": {"apply": (D(2026, 11, 20, 18), D(2026, 11, 22, 18)), "winner": D(2026, 11, 23, 10),
                           "contract": (D(2026, 11, 23, 10), D(2026, 11, 27, 17))}}
        c = bot.slack_item(n, dt.date(2026, 11, 21))
        self.assertEqual(c["rows"], [["A", "1,000", "62.8", "10층", "남향", "도생"]])
        self.assertIn("신청 11/20(금) 18:00~11/22(일) 18:00 · 발표 11/23(월) 10:00 · 계약 11/23(월)~11/27(금)", c["note"])
        for mode in ("data_table", "table", "text"):
            b = SL.notice_blocks(c, mode)
            if mode != "text":
                tb = b[1]
                self.assertTrue(all(len(r) == 6 for r in tb["rows"]))
                if mode == "data_table":
                    self.assertEqual(tb["rows"][1][1], {"type": "raw_number", "value": 1000.0, "text": "1,000"})
                else:
                    self.assertEqual(len(tb["column_settings"]), 6)
            else:
                self.assertIn("A · 보증금 1,000만 · 월 62.8만 · 10층 남향 도생", b[1]["text"]["text"])


class EligBox(unittest.TestCase):
    def test_box_under_table_and_combined(self):
        rows = [{"type": "A", "deposit": 10_000_000, "rent": 628_000}]
        n = {**notice("[어바니엘 천호] 공실세대 모집공고 (접수기간: 11/20~11/22)"), "ely_rows": rows,
             "deadline": "11/22(일)", "elig": [("입주", "12/27까지 가능해야 함"), ("불가", "법인·외국인")]}
        c = bot.slack_item(n, dt.date(2026, 11, 21))
        b = SL.notice_blocks(c, "data_table")
        self.assertEqual([x["type"] for x in b], ["section", "data_table", "section", "context", "actions"])
        self.assertEqual(b[2]["text"]["text"], "*👤 자격*\n```입주  12/27까지 가능해야 함\n불가  법인·외국인```")
        t = SL.notice_blocks(c, "text")[1]["text"]["text"]        # 표를 못 보내면 임대조건·자격을 한 칸에
        self.assertTrue(t.startswith("*🏠 임대조건 · 👤 자격*\n```A · 보증금 1,000만 · 월 62.8만"))
        self.assertIn("불가  법인·외국인```", t)
        c2 = bot.slack_item({**n, "elig": []}, dt.date(2026, 11, 21))
        self.assertEqual(SL.notice_blocks(c2, "data_table")[2]["text"]["text"], "*👤 자격*  공고문 확인")

    def test_note_dedup(self):                      # 비고 '여성만 입주가능'은 자격 칸(성별 여성만)과 겹쳐서 뺀다
        rows = [{"type": "B", "deposit": 15_000_000, "rent": 690_000, "note": "여성만입주가능"}]
        n = {**notice("[어바니엘 한강] 공실세대 모집공고 (접수기간: 11/20~11/22)"), "ely_rows": rows,
             "deadline": "11/22(일)", "elig": [("성별", "여성만")]}
        self.assertNotIn("비고", bot.slack_item(n, dt.date(2026, 11, 21))["note"])
        n2 = {**n, "ely_rows": [{**rows[0], "note": "청년일반공급"}], "elig": [("공급", "일반")]}
        self.assertNotIn("비고", bot.slack_item(n2, dt.date(2026, 11, 21))["note"])


@unittest.skipUnless(OCR.available(), "tesseract 없음")
class ReadImage(unittest.TestCase):
    def test_cheonho(self):                       # 어바니엘 천호 2026-11-20 공고문 이미지
        r = OCR.read_images([(FIX / "elyes_cheonho_261120.jpg").read_bytes()])
        self.assertEqual(r["errors"], [])
        self.assertEqual([(x["type"], x["deposit"], x["rent"], x["floor"], x["dir"], x["use"]) for x in r["rows"]],
                         [("A", 10_000_000, 628_000, "10층", "남향", "도생")])
        self.assertEqual(r["apply"], (D(2026, 11, 20, 18), D(2026, 11, 22, 18)))
        self.assertEqual(r["winner"], D(2026, 11, 23, 10))
        self.assertEqual(r["contract"], (D(2026, 11, 23, 10), D(2026, 11, 27, 17)))


if __name__ == "__main__":
    unittest.main()
