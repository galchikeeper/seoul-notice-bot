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
    def test_apply_period_shown(self):               # 마감만 있고 신청기간이 없던 문제(청량리역 퀸즈W 2026-11-27)
        m = {**n([t("41.40")], deadline="12/1(화) 23:00"), "apply_start": "11/30(월) 00:00"}
        c = bot.slack_item(m, dt.date(2026, 11, 27), fetch_pdf=False)
        self.assertIn("신청 11/30(월) 00:00 ~ 마감 12/1(화) 23:00", c["top"])
        self.assertTrue(any("신청 시작 전" in f for f in c["flags"]))
        c2 = bot.slack_item(n([t("41.40")]), dt.date(2026, 11, 27), fetch_pdf=False)   # 시작을 모르면 예전 표기
        self.assertIn("마감 11/28", c2["top"])
        self.assertNotIn("신청 ", c2["top"])

    def test_apply_start_parse(self):
        import sources as S, reader as R
        self.assertEqual(S._apply_start("‘26. 11. 30. (월) 00:00 ~ 12. 01. (화) 23:00"), "11/30(월) 00:00")
        self.assertEqual(S._apply_start("‘26. 09. 07. (월)"), "")
        self.assertEqual(R.apply_start_from_text("접수기간 2026.11.20.(금) 10:00 ~ 2026.11.22.(일) 18:00"), "11/20(금) 10:00")

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


class Hold(unittest.TestCase):
    """신청 시작 전 공고는 보류, 시작 뒤에는 발송 대상으로(2026-11-27)."""
    def _run(self, start, state):
        from unittest import mock
        import sources as S
        row = {"board_id": 7000, "source": "soco", "title": "[민간임대] 시험역 시험단지 추가모집공고", "deadline": "12/1(화) 23:00",
               "apply_start": start, "url": "u", "addr": "", "pdf": None}
        with mock.patch.object(S, "fetch_soco", return_value=[dict(row)]), \
             mock.patch.object(S, "fetch_hillstate", side_effect=Exception("x")), \
             mock.patch.object(bot, "_not_started", side_effect=lambda s, t: s == "12/30(수) 00:00"):
            return bot.collect(state, dt.date(2026, 11, 27))

    def test_hold_then_release(self):
        st = {**bot.DEFAULT_STATE, "soco_last_board_id": 6999, "hillstate_seen": []}
        news, *_ = self._run("12/30(수) 00:00", st)
        self.assertFalse([n for n in news if n.get("board_id") == 7000])
        self.assertEqual([h["board_id"] for h in st["apply_hold"]], [7000])
        held = st["apply_hold"][0]
        held["apply_start"] = "11/27(금) 00:00"                  # 시작 시각이 지난 뒤 실행
        st2 = {**bot.DEFAULT_STATE, "soco_last_board_id": 7000, "hillstate_seen": [], "apply_hold": [held]}
        news2, *_ = self._run("11/27(금) 00:00", st2)
        self.assertEqual([n["board_id"] for n in news2 if n.get("board_id") == 7000], [7000])
        self.assertEqual(st2["apply_hold"], [])
