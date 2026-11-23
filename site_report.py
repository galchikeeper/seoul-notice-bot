#!/usr/bin/env python3
"""감시 사이트 전체 점검표 — 사이트마다 최근 모집공고 1건을 공통 읽기 순서(reader)로 돌려 본다.

  python site_report.py                 # 표 출력(마크다운)
  python site_report.py --save DIR      # 받은 첨부·결과를 시험 묶음 후보로 저장(DIR/<사이트>-<번호>.*, cases_new.json)

발송·상태 파일 변경 없음. 사본 보관(GitHub 릴리스)도 하지 않는다.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources as S, reader as R, bot

KST = dt.timezone(dt.timedelta(hours=9))


def latest() -> list[tuple[str, dict | None, str]]:
    """(사이트, 공고 n, 못 고른 이유)"""
    out = []
    try:
        x = next(x for x in S.fetch_soco(0, max_pages=1) if x.get("pdf"))
        out.append(("청년안심주택(soco)", {**x, "source": "soco"}, ""))
    except Exception as e:
        out.append(("청년안심주택(soco)", None, f"목록 실패({type(e).__name__})"))
    try:
        x = next(x for x in S.fetch_sh(1) if x["mine"])
        out.append(("SH 주택임대", {"board_id": f"sh:{x['seq']}", "source": "sh", "title": f"SH — {x['title']}",
                                 "raw_title": x["title"], "name": x["title"][:40], "url": x["url"], "pdf": None,
                                 "types": [], "deadline": ""}, ""))
    except Exception as e:
        out.append(("SH 주택임대", None, f"목록 실패({type(e).__name__})"))
    try:
        st = bot.load_state()
        regions = S.elyes_regions(st.get("elyes_regions2", {}))
        x = next(x for x in S.fetch_elyes(regions) if x["images"])
        n = {"board_id": f"elyes:{x['id']}", "source": "elyes", "title": f"롯데 엘리스 — {x['title']}",
             "raw_title": x["title"], "name": x["title"][:40], "url": x["url"], "pdf": None, "types": [],
             "images": x["images"], "posted": x.get("date", "")}
        now = dt.datetime.now(KST).replace(tzinfo=None)
        m = bot.elyes_enrich(n, now) or bot.elyes_enrich(n, now - dt.timedelta(days=30)) or n   # 마감 지난 글도 점검은 한다
        out.append(("롯데 엘리스", m, ""))
    except Exception as e:
        out.append(("롯데 엘리스", None, f"목록 실패({type(e).__name__})"))
    try:
        h = S.fetch_hillstate()
        x = next((x for x in h["rows"] if x["is_recruit"]), h["rows"][0])
        out.append(("힐스테이트 관악 뉴포레", {"board_id": f"hill:{x['no']}", "source": "hillstate",
                                         "title": f"힐스테이트 관악 뉴포레 — {x['title']}", "raw_title": x["title"],
                                         "url": urljoin(h["url"], x["href"]) if x["href"] else h["url"],
                                         "pdf": None, "types": [], "deadline": ""}, ""))
    except Exception as e:
        out.append(("힐스테이트 관악 뉴포레", None, f"목록 실패({type(e).__name__})"))
    if os.environ.get("MYHOME_API_KEY", "").strip():
        try:
            import myhome as MH
            x = next(x for x in MH.fetch_notices(dt.datetime.now(KST).date(), log=lambda *_: None) if S.mine(x["title"]))
            out.append(("마이홈(공공데이터 API)", {"board_id": f"myhome:{x['id']}", "source": "myhome",
                                              "title": f"마이홈 — {x['title']}", "raw_title": x["title"], "url": x["url"],
                                              "pdf": None, "types": x["types"], "deadline": x["deadline"]}, ""))
        except Exception as e:
            out.append(("마이홈(공공데이터 API)", None, f"목록 실패({type(e).__name__})"))
    else:
        out.append(("마이홈(공공데이터 API)", None, "API 키 없음(이 실행 환경)"))
    try:
        sl, name = next(iter(S.SPACEREITS.items()))
        r = S.fetch_spacereits(sl)
        if r.get("error"):
            out.append(("공간지원리츠(5개 단지)", None, f"목록 실패({r.get('why')})"))
        else:
            out.append(("공간지원리츠(5개 단지)", {"board_id": f"space:{sl}", "source": "spacereits",
                                              "title": f"{name} — {r['title']}", "raw_title": r["title"], "url": r["url"],
                                              "pdf": None, "types": [], "deadline": ""}, ""))
    except Exception as e:
        out.append(("공간지원리츠(5개 단지)", None, f"목록 실패({type(e).__name__})"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", default="")
    a = ap.parse_args()
    today = dt.datetime.now(KST).date()
    rows, cases = [], []
    for site, n, why in latest():
        if not n:
            rows.append([site, "-", "-", "-", "-", f"❌ {why}"])
            continue
        card, res, probs = __import__("gate").prepare(n, lambda m: bot.slack_item(m, today, fetch_pdf=False))
        files = res.get("files", [])
        kinds = ", ".join(sorted({f["kind"].upper() for f in files if f.get("kind")})) or "없음"
        link = "✅" if not res.get("page_problem") else f"❌ {res['page_problem'][:40]}"
        cond = []
        cond.append("임대조건 " + ("✅" if card.get("rows") else "제외(전용 30㎡ 미만만 — 발송 안 함)" if card.get("skip") else "❌"))
        cond.append("자격 " + ("✅" if card.get("elig_items") else "❌"))
        cond.append("마감 " + ("❌" if ("공고문 확인" in card["top"] or "확인 필요" in card["top"]) else "✅"))
        real = [p for p in probs if not p.startswith("파일 — 받았지만")]      # 이 점검은 사본 보관을 하지 않음
        verdict = "✅ 통과" if not real else "⚠️ " + "; ".join(p.split(" — ")[0] for p in real)
        rows.append([site, (n.get("raw_title") or n.get("title", ""))[:28], kinds, link, " · ".join(cond), verdict])
        if a.save:
            d = Path(a.save)
            d.mkdir(parents=True, exist_ok=True)
            saved = []
            for i, f in enumerate([f for f in files if f.get("data")][:3], 1):
                p = d / f"{n.get('source')}-{str(n.get('board_id','x')).split(':')[-1][:20]}-{i}.{f['kind']}"
                p.write_bytes(f["data"])
                saved.append({"file": p.name, "name": f["name"]})
            cases.append({"site": site, "source": n.get("source"), "title": n.get("raw_title") or n.get("title"),
                          "url": n.get("url"), "files": saved, "got": {
                              "rows": card.get("rows"), "elig": card.get("elig_items"), "top": card["top"]},
                          "problems": probs, "date": str(today)})
    print("| 사이트 | 시험 공고 | 첨부 형태 | 링크 열림 | 조건 추출 | 판정 |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
    if a.save:
        (Path(a.save) / "cases_new.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
