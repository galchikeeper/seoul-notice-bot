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


class MacQueue(unittest.TestCase):
    """마이홈 카드는 GitHub 에서 보내지 않고 맥북 대기열로(2026-11-24)."""
    def test_item_json_and_key(self):
        n = {"board_id": "myhome:1", "source": "myhome", "name": "강서", "when": TODAY}
        q = bot.myhome_queue_item(n, "urgent", TODAY)
        self.assertEqual(q["key"], "myhome:1|urgent|2026-11-19")
        self.assertEqual(q["n"]["when"], "2026-11-19")        # 날짜는 글자로 — state.json 저장이 깨지지 않게

    def test_merge_dedup_and_expire(self):
        a = bot.myhome_queue_item({"board_id": "myhome:1"}, "new", TODAY)
        old = [a, {"key": "myhome:0|new|2026-11-01", "queued": "2026-11-01", "n": {}}]
        out = bot.myhome_queue_merge(old, [a], TODAY)
        self.assertEqual([q["key"] for q in out], [a["key"]])  # 같은 key 하나 · 14일 지난 것 뺌



class MyhomeNoDownload(unittest.TestCase):
    """robots.txt 가 마이홈 공고문 내려받기를 금지 — 첨부 주소를 만들지 않고, 관문은 파일 없음을 실패로 안 센다."""
    def test_no_attachment_url(self):
        import reader as R
        html = '<td><a href="javascript:fnDownFile(\'1a20750e60a5\', \'1\')">공고문.pdf</a></td>'
        self.assertEqual(R.attachments({"source": "myhome"}, html, "https://www.myhome.go.kr/x"), [])

    def test_read_never_downloads(self):         # 일반 href 로 된 내려받기 링크도 요청하지 않는다(검수 1/2 지적)
        import reader as R
        html = '<a href="/hws/com/fms/cvplFileDownload.do?atchFileId=a&fileSn=1">공고문.pdf</a>'
        class Resp:
            status_code, text, headers = 200, html, {"content-type": "text/html"}
        calls = []
        def fake_get(u, **k):
            calls.append(u)
            return Resp()
        old_get, old_pp = R.get, R.page_problem
        R.get, R.page_problem = fake_get, lambda r, t="": ""
        try:
            res = R.read({"source": "myhome", "url": "https://www.myhome.go.kr/p", "title": "t"})
        finally:
            R.get, R.page_problem = old_get, old_pp
        self.assertEqual(calls, ["https://www.myhome.go.kr/p"])
        self.assertEqual(res["files"], [])

    def test_gate_skips_file(self):
        import gate as G
        card = {"rows": [["a"]], "elig_items": ["주택 무주택"], "top": "x · 마감 2026-12-01", "notice_url": "u"}
        res = {"page_url": "u", "files": [], "no_files_reason": "금지"}
        self.assertEqual(G.check(card, res, []), [])
        self.assertIn("파일 — 공고 페이지에서 첨부를 찾지 못함", G.check(card, {"page_url": "u", "files": []}, []))



class MyhomeArea(unittest.TestCase):
    """마이홈 페이지 스크립트의 공급 목록으로 타입별 전용면적·금액을 채운다(API 에는 면적 없음, 2026-11-24)."""
    HTML = ('houseList.push({ pblancId : "21598", houseSn : "4", hsmpNm : "서울삼전" });'
            'suplyList.push({ pblancId : "21598", houseSn : "4", styleNm : "26", prvuseAr : "26.21", rentGtn : "84400000", mtRntchrg : "351660" });'
            'suplyList.push({ pblancId : "21598", houseSn : "4", styleNm : "41", prvuseAr : "41.39", rentGtn : "120400000", mtRntchrg : "501660" });'
            'suplyList.push({ pblancId : "21598", houseSn : "5", styleNm : "36B", prvuseAr : "36.33", rentGtn : "102340000", mtRntchrg : "383770" });')

    def test_types(self):
        import reader as R
        class Resp:
            text = 'houseList.push({ pblancId : "21598", houseSn : "5", hsmpNm : "강동천호" });'
        old = R.get
        R.get = lambda u, **k: Resp()
        try:
            ts = R.myhome_types(self.HTML, "https://www.myhome.go.kr/x", kind="행복주택")
        finally:
            R.get = old
        self.assertEqual([(t["label"], t["area"], t["prices"][0]["deposit"], t["prices"][0]["rent"]) for t in ts],
                         [("서울삼전 26", "26.21", 8440.0, 35.17), ("서울삼전 41", "41.39", 12040.0, 50.17),
                          ("강동천호 36B", "36.33", 10234.0, 38.38)])
        c = bot.slack_item({"title": "t", "name": "t", "deadline": "2026-12-01", "types": ts, "url": "u", "pdf": None},
                           TODAY, fetch_pdf=False)
        self.assertEqual(c["flags"], [])                               # 면적이 있으니 📐 없음
        self.assertEqual([r[0] for r in c["rows"]], ["서울삼전 41", "강동천호 36B"])   # 30㎡ 미만 제외

    def test_no_list(self):                       # 매입임대(다가구)처럼 목록이 없으면 API 값 그대로
        import reader as R
        self.assertEqual(R.myhome_types("<html></html>", "https://www.myhome.go.kr/x"), [])
