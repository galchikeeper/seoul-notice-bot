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
        self.assertIsNone(c["rows"])                         # '확인 실패'만 찬 표 대신 한 줄
        self.assertEqual(c["fallback_text"], "임대조건: 공고문 확인")
        self.assertIn("📐 면적 확인 필요", c["flags"])

    def test_urgent_top_line(self):
        c = bot.slack_item(n([t("43.61")], deadline="11/19(목) 18:00"), TODAY, fetch_pdf=False)
        self.assertEqual(c["top"], "⚠️ D-1 · 시험단지 · 마감 11/19(목) 18:00")


if __name__ == "__main__":
    unittest.main()


class Partial(unittest.TestCase):
    def test_partial_table_not_skipped(self):       # 20㎡ 표만 읽히고 다른 표는 실패 → 빼지 않고 확인 필요 표시
        x = n([t("20.20")]); x["types_complete"] = False
        c = bot.slack_item(x, TODAY, fetch_pdf=False)
        self.assertFalse(c["skip"])
        self.assertIn("📐 면적 확인 필요", c["flags"])
        self.assertIsNone(c["rows"])
        self.assertEqual(c["fallback_text"], "임대조건: 공고문 확인")


class Send(unittest.TestCase):
    def setUp(self):
        import slack
        self.slack = slack
        slack.time.sleep = lambda s: None
        self.calls = []

    def fake_requests(self, responses):
        calls = self.calls
        class R:
            def __init__(self, code, text="", headers=None):
                self.status_code, self.text, self.headers = code, text, headers or {}
        seq = iter(responses)
        def post(url, json=None, timeout=None):
            calls.append([b["type"] for b in json.get("blocks", [])][1:2])
            nx = next(seq)
            if isinstance(nx, Exception):
                raise nx
            return R(*nx)
        self.slack.requests.post = post

    def card(self):
        return {"top": "단지 · 마감 11/28", "flags": [], "rows": [["54형", "43.61", "청년", "30%", "10,790", "90"]],
                "caption": "c", "note": "n", "fallback_text": "f", "notice_url": "https://e.invalid", "apply_url": ""}

    def test_invalid_blocks_falls_back(self):
        self.fake_requests([(400, "invalid_blocks"), (200, "ok")])
        s = self.slack.Slack("https://hooks.invalid/x")
        self.assertTrue(s.send_notice(self.card()))
        self.assertEqual(self.calls, [["data_table"], ["table"]])

    def test_429_waits_same_format(self):
        self.fake_requests([(429, "", {"Retry-After": "2"}), (200, "ok")])
        s = self.slack.Slack("https://hooks.invalid/x")
        self.assertTrue(s.send_notice(self.card()))
        self.assertEqual(self.calls, [["data_table"], ["data_table"]])

    def test_timeout_no_resend(self):
        self.fake_requests([TimeoutError("t")])
        s = self.slack.Slack("https://hooks.invalid/x")
        self.assertFalse(s.send_notice(self.card()))
        self.assertEqual(len(self.calls), 1)
