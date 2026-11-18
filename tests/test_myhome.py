"""마이홈 API 출처: 지역 범위·금액 단위·공고 묶기·중복 합치기.  실행: python -m unittest discover tests"""
import datetime as dt, pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import myhome as MH
import bot

TODAY = dt.date(2026, 11, 19)


def item(pid="1", brtc="서울특별시", sg="강서구", addr="", name="강서 행복주택", nm="강서 행복주택 입주자 모집공고",
         dep="20000000", rent="150000", end="20261130", ty="행복주택"):
    return {"pblancId": pid, "brtcNm": brtc, "signguNm": sg, "fullAdres": addr, "hsmpNm": name, "pblancNm": nm,
            "rentGtn": dep, "mtRntchrg": rent, "endDe": end, "suplyTyNm": ty, "suplyInsttNm": "SH", "pcUrl": "https://e.invalid/p"}


class Area(unittest.TestCase):
    def test_area(self):
        self.assertTrue(MH.in_area(item()))
        self.assertTrue(MH.in_area(item(brtc="경기도", sg="성남시 분당구")))
        self.assertTrue(MH.in_area(item(brtc="경기도", sg="하남시")))
        self.assertFalse(MH.in_area(item(brtc="경기도", sg="수원시 팔달구")))
        self.assertFalse(MH.in_area(item(brtc="경기도", sg="용인시 수지구")))
        self.assertFalse(MH.in_area(item(brtc="인천광역시", sg="미추홀구")))
        self.assertIsNone(MH.in_area(item(brtc="경기도", sg="", addr="")))
        self.assertTrue(MH.in_area(item(brtc="경기도", sg="", addr="경기도 과천시 갈현동")))


class Money(unittest.TestCase):
    def test_money(self):
        self.assertEqual(MH.money("201,700,000", "dep"), 20170)
        self.assertEqual(MH.money("253200", "rent"), 25.32)
        self.assertIsNone(MH.money("9800", "dep"))           # 단위를 모르는 작은 값 → 짐작하지 않음
        self.assertIsNone(MH.money("100000", "dep"))
        self.assertIsNone(MH.money("0", "dep"))
        self.assertIsNone(MH.money("", "rent"))
        self.assertIsNone(MH.money("300", "rent"))


class Group(unittest.TestCase):
    def test_group_filter_expired(self):
        xs = [item("1"), item("1", brtc="경기도", sg="수원시", name="수원 단지"),
              item("2", brtc="경기도", sg="파주시"), item("3", end="20261101"),
              item("4", brtc="경기도", sg="", addr="")]
        out = MH.notices(xs, TODAY, log=lambda *a: None)
        self.assertEqual([n["id"] for n in out], ["1"])        # 2 범위 밖 · 3 마감 · 4 지역 불명
        self.assertEqual([t["label"] for t in out[0]["types"]], ["강서 행복주택"])   # 수원 단지는 뺌
        self.assertEqual(out[0]["days_left"], 11)

    def test_card(self):
        x = MH.notices([item(dep="0")], TODAY, log=lambda *a: None)[0]
        n = {"title": x["title"], "name": x["title"], "deadline": x["deadline"], "days_left": x["days_left"],
             "types": x["types"], "url": x["url"], "apply_url": x["url"], "pdf": None}
        c = bot.slack_item(n, TODAY, fetch_pdf=False)
        self.assertEqual(c["flags"], ["📐 면적 확인 필요"])
        self.assertFalse(c["skip"])
        self.assertEqual(c["rows"][0][1], "확인 실패")             # 면적
        self.assertEqual(c["rows"][0][4], "확인 실패")             # 보증금 0 → 채우지 않음
        self.assertEqual(c["rows"][0][5], "15")                    # 150,000원 → 15만원


class Dedupe(unittest.TestCase):
    def test_myhome_dup_dropped(self):
        news = [{"source": "sh", "title": "SH — 2026년 마포구 신혼부부,청년(수요자맞춤형)주택 잔여세대 입주자 모집 공고",
                 "raw_title": "2026년 마포구 신혼부부,청년(수요자맞춤형)주택 잔여세대 입주자 모집 공고"},
                {"source": "myhome", "title": "마이홈 — x", "raw_title": "2026년 마포구 신혼부부·청년(수요자맞춤형)주택 잔여세대 입주자 모집 공고문", "source": "myhome"}]
        news[1]["days_left"], news[1]["dday"], news[1]["urgent"], news[1]["deadline"] = 1, "D-1", True, "2026-11-20"
        out = bot.dedupe(news, {})
        self.assertEqual([n["source"] for n in out], ["sh"])
        self.assertTrue(out[0]["urgent"])                   # 마감 정보는 남는 쪽으로 옮김
        self.assertEqual(out[0]["dday"], "D-1")

    def test_soco_same_title_kept(self):           # 청년안심 '추가모집공고'는 같은 제목으로 매달 새로 올라온다
        t = "[민간임대] 상봉역 상봉생활 추가모집공고"
        st = {"alerted_titles": [bot.tnorm(t)]}
        out = bot.dedupe([{"source": "soco", "title": t}], st)
        self.assertEqual(len(out), 1)

    def test_myhome_dup_previous_run(self):
        t = "강서 행복주택 입주자 모집공고 2026년 11월"
        out = bot.dedupe([{"source": "myhome", "title": "마이홈 — " + t, "raw_title": t}], {"alerted_titles": [bot.tnorm(t)]})
        self.assertEqual(out, [])


class Dedupe2(unittest.TestCase):
    def test_corrected_notice_not_dropped(self):    # 앞에 [정정]이 붙은 새 공고는 중복이 아니다
        old = "서울 강서 행복주택 입주자 모집공고"
        out = bot.dedupe([{"source": "myhome", "title": "x", "raw_title": "[정정] " + old}],
                         {"alerted_titles": [bot.tnorm(old)]})
        self.assertEqual(len(out), 1)

    def test_reverse_order(self):                   # 마이홈으로 먼저 알린 뒤 SH 에 같은 공고 → SH 쪽 생략
        t = "2026년 강서구 청년 매입임대주택 입주자 모집 공고"
        st = {}
        bot.dedupe([{"source": "myhome", "title": "x", "raw_title": t}], st)
        st["myhome_alerted"] = st.pop("_new_myhome_alerted")
        out = bot.dedupe([{"source": "sh", "title": "SH — " + t, "raw_title": t}], st)
        self.assertEqual(out, [])

    def test_redact_decoded(self):
        import os
        os.environ["MYHOME_API_KEY"] = "FAKE%2BKEY%2FVALUE%3D"
        try:
            self.assertNotIn("FAKE+KEY/VALUE=", MH.redact("err FAKE+KEY/VALUE= x"))
        finally:
            del os.environ["MYHOME_API_KEY"]


if __name__ == "__main__":
    unittest.main()
