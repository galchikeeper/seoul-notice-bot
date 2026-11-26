"""고정 시험 묶음(tests/regress/cases.json) — 무엇을 고치든 전부 통과해야 배포.

  python -m pytest -q tests                      # 파일 케이스(네트워크 없음)
  REGRESS_LIVE=1 python -m pytest -q tests       # + 실제 사이트에서 공고 페이지·첨부 받기까지

엘리스 이미지 OCR 값은 GitHub Actions(ubuntu, tesseract 5.5.0) 기준이다. 맥(5.5.4)은 같은 칸을 다르게 읽어
엘리스 케이스가 실패할 수 있다 — 그래서 배포 판정은 Actions 의 '시험' 워크플로 결과로 한다(deploy.sh).
"""
import datetime as dt, json, os, pathlib, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("READ_WAITS", "0")
import reader as R, gate as G, bot, elyes_ocr as OCR

FIX = ROOT / "tests" / "fixtures" / "regress"
SPEC = json.loads((ROOT / "tests" / "regress" / "cases.json").read_text())
TODAY = dt.date(2026, 11, 23)


def _res(case):
    files, texts = [], []
    for nm in case["files"]:
        data = (FIX / nm).read_bytes()
        kind, text, how = R.extract(nm, data)
        files.append({"name": nm, "url": "", "kind": kind, "size": len(data), "how": how,
                      "chars": len(R._ns(text)), "data": data})
        texts.append(text)
    return {"page_url": case.get("url", ""), "page_problem": "", "files": files, "text": "\n".join(texts), "problems": []}


@pytest.mark.parametrize("case", [c for c in SPEC["cases"] if c["source"] != "elyes"], ids=lambda c: c["id"])
def test_file_case(case):
    exp = case["expect"]
    res = _res(case)
    if "text_method" in exp:
        assert exp["text_method"] in res["files"][0]["how"]
    n = {"source": case["source"], "title": case.get("title", case["id"]), "raw_title": case.get("title", ""),
         "url": case.get("url", "https://example.invalid"), "pdf": None, "types": [], "deadline": ""}
    m = R.fill(n, res)
    got = [[t["label"], t["area"], p["deposit"], p["rent"]] for t in m["types"] for p in t["prices"]]
    num = lambda v: v if isinstance(v, str) else float(v)          # '주택별 상이' 같은 설명 글자는 그대로 비교
    assert got == [[a, b, num(c), num(d)] for a, b, c, d in exp["rows"]], got
    if "deadline" in exp:
        assert m.get("deadline", "") == exp["deadline"]
    keys = [k for k, _ in m["elig"]]
    for k in exp.get("elig_keys", []):
        assert k in keys, (k, m["elig"])
    for k, v in exp.get("elig_values", {}).items():
        assert dict(m["elig"]).get(k) == v, m["elig"]
    card = bot.slack_item(m, TODAY, fetch_pdf=False)
    probs = G.check(card, res, archived=[{"url": "x"}])
    if exp.get("gate_ok"):
        assert probs == [], probs
    if exp.get("deadline") == "":
        assert any(p.startswith("마감") for p in probs), probs     # 못 고른 마감은 반드시 카드에 표시


@pytest.mark.parametrize("case", [c for c in SPEC["cases"] if c["source"] == "elyes"], ids=lambda c: c["id"])
def test_elyes_case(case):
    if not OCR.available():
        pytest.skip("tesseract 없음")
    exp = case["expect"]
    r = OCR.read_images([(FIX / nm).read_bytes() for nm in case["files"]])
    assert [[x["type"], x["deposit"], x["rent"]] for x in r["rows"]] == exp["ely_rows"], r
    assert r["apply"][1] == dt.datetime.fromisoformat(exp["apply_end"])


def test_gate_marks_broken_card():
    """조건을 하나도 못 채운 카드는 관문이 '무엇 — 왜'를 돌려준다(표시 없이 나가지 않음)."""
    res = {"page_problem": "HTTP 403", "files": [], "text": ""}
    card = {"top": "x · 마감 공고문 확인", "rows": None, "elig_items": [], "notice_url": ""}
    probs = G.check(card, res, [])
    heads = [p.split(" — ")[0] for p in probs]
    assert heads == ["링크", "파일", "임대조건", "자격", "마감"], probs


def test_gate_skip_card_is_not_rent_failure():
    """30㎡ 미만 타입만 있어 보내지 않는 카드(엘리스 염창)는 '임대조건' 실패로 세지 않는다."""
    res = {"page_problem": "", "files": [{"name": "a.jpg", "kind": "jpg", "data": b"x", "how": "", "chars": 0}], "text": ""}
    card = {"top": "x · 마감 11/22", "rows": None, "skip": True, "elig_items": [("a", "b")], "notice_url": ""}
    assert G.check(card, res, [{"url": "u"}]) == []


def test_empty_columns_dropped():
    import slack as SL
    head, rows = SL._rows_for({"rows": [["2F 101호", "47.28", "", "-", "19,000", "79"]]})
    assert head == ["타입", "전용㎡", "보증금(만원)", "월세(만원)"] and rows == [["2F 101호", "47.28", "19,000", "79"]]
    head, _ = SL._rows_for({"rows": [["A", "36", "청년", "50%", "1", "2"]]})
    assert len(head) == 6


def test_asset_names_do_not_collide():
    """같은 날 한글 제목 공고 둘(공간지원리츠)의 사본 이름이 겹치지 않는다(검수 2/2 지적)."""
    import archive as A
    a = A.asset_name({"source": "spacereits", "board_id": "space:26.11.23|서울 A 공고"}, 1, "pdf")
    b = A.asset_name({"source": "spacereits", "board_id": "space:26.11.23|서울 B 공고"}, 1, "pdf")
    assert a != b and a.isascii() and b.isascii()
    sh = {"source": "sh", "board_id": "sh:313647"}
    assert A.asset_name(sh, 1, "pdf") == A.asset_name(dict(sh), 1, "pdf")       # 같은 공고는 늘 같은 이름


def test_slack_shows_gate_lines():
    import slack as SL
    c = {"top": "t", "flags": [], "rows": None, "fallback_text": "임대조건: 공고문 확인", "note": "n",
         "notice_url": "https://a.b/c", "apply_url": "", "elig_items": [], "gate": ["임대조건 — 못 읽음(pdf)"],
         "files": [{"url": "https://github.com/x/y.pdf", "kind": "pdf"}]}
    blocks = SL.notice_blocks(c, "text", "box")
    s = json.dumps(blocks, ensure_ascii=False)
    assert "자동 점검 실패" in s and "임대조건 — 못 읽음" in s and "공고문 사본" in s


@pytest.mark.skipif(os.environ.get("REGRESS_LIVE") != "1", reason="REGRESS_LIVE=1 일 때만(실제 사이트 접속)")
@pytest.mark.parametrize("case", SPEC["live"], ids=lambda c: c["id"])
def test_live(case):
    os.environ["READ_WAITS"] = "3,10,20"
    res = R.read({"source": case["source"], "title": case["title"], "raw_title": case["title"],
                  "url": case["url"], "types": [], "elig": [("x", "y")], "deadline": "x"}, tries=3)
    if res["page_problem"].startswith(("열리지 않음", "봇 실행 서버")):
        pytest.skip(f"사이트 접속 안 됨(코드 문제 아님): {res['page_problem']}")   # 오류 화면·제목 불일치는 실패로 남김
    assert res["page_problem"] == "", res["page_problem"]
    assert res["files"], "첨부를 찾지 못함"
    f = res["files"][0]
    assert f["kind"] == case["first_file_kind"] and case["first_file_name_has"] in f["name"], f["name"]
