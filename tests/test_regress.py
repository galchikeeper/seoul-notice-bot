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
    assert got == [[a, b, float(c), float(d)] for a, b, c, d in exp["rows"]], got
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
    assert res["page_problem"] == "", res["page_problem"]
    assert res["files"], "첨부를 찾지 못함"
    f = res["files"][0]
    assert f["kind"] == case["first_file_kind"] and case["first_file_name_has"] in f["name"], f["name"]
