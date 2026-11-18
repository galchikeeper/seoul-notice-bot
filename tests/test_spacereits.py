"""공간지원리츠: 접속이 안 될 때 오류 종류를 남기고, 나머지 단지는 기다리지 않고 건너뛴다."""
import time, unittest
from unittest import mock
import requests
import sources as S


class SpacereitsTest(unittest.TestCase):
    def test_timeout_is_unreachable_with_reason(self):
        calls = []
        def boom(url, **kw):
            calls.append(kw.get("timeout"))
            raise requests.exceptions.ConnectTimeout("x")
        with mock.patch.object(S.requests, "get", boom), mock.patch("time.sleep"):
            r = S.fetch_spacereits("seongdong")
        self.assertTrue(r["error"] and r["unreachable"])
        self.assertEqual(r["why"], "ConnectTimeout")
        self.assertEqual(calls, [10, 10, 10])          # 3번 시도, 시간 제한 10초

    def test_other_error_is_not_unreachable(self):
        resp = mock.Mock(status_code=500)
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        with mock.patch.object(S.requests, "get", return_value=resp), mock.patch("time.sleep"):
            r = S.fetch_spacereits("seongdong")
        self.assertTrue(r["error"])
        self.assertFalse(r["unreachable"])
        self.assertEqual(r["why"], "HTTPError")

    def test_collect_skips_rest_when_unreachable(self):
        """실제 bot.collect: 모든 접속이 시간 초과일 때 공간지원리츠는 첫 단지만 3번 시도하고 멈춘다."""
        import datetime as dt, os, bot
        urls = []
        def boom(url, *a, **kw):
            urls.append(url)
            raise requests.exceptions.ConnectTimeout("x")
        state = bot.load_state()
        with mock.patch.object(requests, "get", boom), mock.patch.object(requests, "post", boom), \
             mock.patch("time.sleep"), mock.patch.dict(os.environ, {"MYHOME_API_KEY": ""}):
            out = bot.collect(state, dt.date(2026, 11, 19))
        errors = out[1]
        space = [u for u in urls if "spacereits" in u]
        self.assertEqual(len(space), 3)
        self.assertTrue(all("/seongdong/" in u for u in space))
        self.assertIn("공간지원리츠 접속 안 됨(ConnectTimeout)", errors)
        self.assertFalse(any("스페이스 확인 실패" in e for e in errors))

if __name__ == "__main__":
    unittest.main()
