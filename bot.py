#!/usr/bin/env python3
"""서울 공공지원민간임대·청년안심주택 신규 공고 → 카카오톡 카드 알림.

GitHub Actions 에서 평일 08:00 KST 에 실행된다.
  1) state.json 의 마지막 확인 지점을 읽고
  2) 소스 4곳에서 신규 공고를 찾고
  3) 상위 N건은 공고문 PDF까지 열어 카드 이미지를 만들고
  4) 카카오톡 '나에게 보내기' 피드 템플릿으로 이미지째 보내고
  5) state.json 과 카드 이미지를 저장소에 커밋한다.
"""
from __future__ import annotations
import asyncio, datetime as dt, json, os, re, sys, unicodedata
from pathlib import Path

import sources as S
from pdfparse import fetch_types
from card import render
from kakao import Kakao, update_github_secret

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state.json"
CARDS = ROOT / "cards"
KST = dt.timezone(dt.timedelta(hours=9))

CARD_LIMIT = int(os.environ.get("CARD_LIMIT", "3"))   # 카드로 보낼 최대 건수
REPO = os.environ.get("GITHUB_REPOSITORY", "")        # owner/name
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
CDN = "https://cdn.jsdelivr.net/gh/{repo}@{branch}/cards/{name}"

DEFAULT_STATE = {
    "soco_last_board_id": 6645,
    "hillstate_count": 28,
    "spacereits": {k: "" for k in S.SPACEREITS},
    "checked": "",
}


# ─────────────────────────── 유틸 ───────────────────────────

def load_state() -> dict:
    if STATE.exists():
        s = json.loads(STATE.read_text(encoding="utf-8"))
        return {**DEFAULT_STATE, **s}
    return dict(DEFAULT_STATE)


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def slug(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"\[.*?\]", "", t)
    t = re.sub(r"[^0-9A-Za-z가-힣]+", "-", t).strip("-")
    return t[:40] or "notice"


def kind_of(title: str) -> str:
    if "최초" in title:
        return "최초모집"
    if "추가" in title:
        return "추가모집"
    if "예비" in title:
        return "예비임차인"
    return "모집공고"


def priority(n: dict) -> int:
    """작을수록 먼저. 마감임박 > 아파트형 > 최초모집 > 회전빠른단지 > 나머지"""
    if n.get("urgent"):
        return 0
    if n["source"] in ("hillstate", "spacereits"):
        return 1
    t = n.get("title", "")
    if "최초" in t:
        return 2
    if any(k in t for k in S.FAST_TURNOVER):
        return 3
    return 4


# ─────────────────────────── 수집 ───────────────────────────

def collect(state: dict, today: dt.date) -> list[dict]:
    news: list[dict] = []
    errors: list[str] = []

    # (A) 청년안심주택
    try:
        for n in S.fetch_soco(state["soco_last_board_id"]):
            n["dday"] = S.dday(n["deadline"], today)
            n["urgent"] = bool(re.match(r"D-([0-3])$", n["dday"] or ""))
            news.append(n)
    except Exception as e:
        errors.append(f"청년안심주택 수집 실패: {e}")

    # (B) 힐스테이트 관악 뉴포레
    try:
        h = S.fetch_hillstate()
        if h["count"] > state["hillstate_count"]:
            news.append({
                "source": "hillstate", "title": f"힐스테이트 관악 뉴포레 — {h['latest']}",
                "name": "힐스테이트 관악 뉴포레", "kicker": "신규 공지 · 공공지원민간임대",
                "addr": "서울 관악구 조원로 25", "station": "신림동",
                "supply": "1,143세대 · 전용 44/59/84㎡", "operator": "서울리츠4호 · KT리빙",
                "deadline": "", "dday": "", "urgent": True, "types": [],
                "url": h["url"], "site": "hillstatenewfore.co.kr", "pdf": None,
            })
        state["_hillstate_count_new"] = h["count"]
    except Exception as e:
        errors.append(f"힐스테이트 뉴포레 확인 실패: {e}")

    # (C) 공간지원리츠
    for sl, name in S.SPACEREITS.items():
        r = S.fetch_spacereits(sl)
        if r.get("error"):
            errors.append(f"{name} 확인 실패")
            continue
        prev = state["spacereits"].get(sl, "")
        if r["latest"] and r["latest"] != prev:
            news.append({
                "source": "spacereits", "title": f"{name} — {r['latest'][:60]}",
                "name": name, "kicker": "신규 공지 · 공공지원민간임대",
                "addr": name, "station": "", "supply": "", "operator": "공간지원리츠",
                "deadline": "", "dday": "", "urgent": "모집공고" in r["latest"],
                "types": [], "url": r["url"], "site": "spacereits.co.kr", "pdf": None,
            })
        state.setdefault("_space_new", {})[sl] = r["latest"]

    news.sort(key=priority)
    return news, errors


# ─────────────────────────── 카드 ───────────────────────────

def clean_name(raw: str) -> str:
    """'[민간임대] 태릉입구역 세이지움 태릉입구역 최초모집공고' → '세이지움 태릉입구역'"""
    s = re.sub(r"^\[.*?\]\s*", "", raw)
    s = re.sub(r"\s*(최초|추가|예비임차인)?\s*모집\s*공고.*$", "", s).strip()
    toks, seen = [], set()
    for t in s.split():                      # 역명이 두 번 들어간 공고명이 흔하다
        if t in seen:
            continue
        seen.add(t)
        toks.append(t)
    return " ".join(toks).strip()


def short_supply(s: str) -> str:
    """'총 927세대 중 공공지원민간임대 546세대 (특별공급 111세대, 일반공급 435세대)'
       → '546세대 · 특별 111 / 일반 435'"""
    if not s:
        return ""
    m = re.search(r"공공지원민간임대\s*([\d,]+)\s*세대", s) or re.search(r"([\d,]+)\s*세대", s)
    head = f"{m.group(1)}세대" if m else s[:20]
    sp = re.search(r"특별\S*\s*([\d,]+)", s)
    gn = re.search(r"일반\S*\s*([\d,]+)", s)
    if sp and gn:
        return f"{head} · 특별 {sp.group(1)} / 일반 {gn.group(1)}"
    return head


def card_data(n: dict, today: dt.date) -> dict:
    name = n.get("name") or clean_name(n["title"])

    types = n.get("types") or []
    if not types and n.get("pdf"):
        types = fetch_types(n["pdf"])
    rows = [{"name": t["label"], "area": t["area"],
             "deposit": t["deposit"], "rent": t["rent"]} for t in types]
    if not rows:
        rows = [{"name": "평형별 조건", "area": "공고문 PDF 확인",
                 "deposit": "—", "rent": "—"}]

    quals = ["만 19~39세 무주택", "청년 미혼 / 신혼 7년 이내",
             "소득·자산 요건 충족", "공고문에서 최종 확인"]

    return {
        "kicker": n.get("kicker") or f"{kind_of(n['title'])} · 청년안심주택",
        "name": name[:22],
        "subtitle": f"공공지원민간임대 · {n.get('posted') or today} 공고",
        "deadline": n.get("deadline") or "공고문 확인",
        "dday": n.get("dday") or "확인",
        "addr": n.get("addr") or "공고문 확인",
        "station": n.get("station") or "",
        "supply": short_supply(n.get("supply", "")) or "공고문 확인",
        "operator": (n.get("operator") or "")[:28],
        "types": rows,
        "quals": quals,
        "url": n.get("site") and f"https://{n['site'].replace('https://','')}" or n["url"],
        "url_label": (n.get("site") or n["url"]).replace("https://", "")[:40],
    }


# ─────────────────────────── 메인 ───────────────────────────

def main() -> int:
    today = dt.datetime.now(KST).date()
    state = load_state()
    news, errors = collect(state, today)

    rest_key = os.environ.get("KAKAO_REST_KEY", "")
    refresh = os.environ.get("KAKAO_REFRESH_TOKEN", "")
    secret  = os.environ.get("KAKAO_CLIENT_SECRET", "")
    dry = os.environ.get("DRY_RUN") == "1" or not (rest_key and refresh)

    kk = None
    if not dry:
        kk = Kakao(rest_key, refresh, secret)
        kk.refresh()

    stamp = today.strftime("%-m/%-d")
    urgent = [n for n in news if n.get("urgent")]

    # 1) 헤더
    if not news:
        head = (f"{stamp} 확인 완료\n신규 공고 없음\n"
                f"청년안심 {state['soco_last_board_id']} · 뉴포레 {state['hillstate_count']}건")
    else:
        head = f"{stamp} 서울 임대주택\n신규 {len(news)}건 · 마감임박 {len(urgent)}건"
    if errors:
        head += "\n⚠ 확인 실패 " + str(len(errors)) + "건"
    print(head, "\n---")
    if kk:
        kk.send_text(head)

    # 2) 카드
    sent = 0
    CARDS.mkdir(exist_ok=True)
    for n in news[:CARD_LIMIT]:
        d = card_data(n, today)
        fname = f"{today:%Y%m%d}-{slug(d['name'])}.png"
        path = CARDS / fname
        asyncio.run(render(d, str(path)))
        print("card:", fname)

        if kk and REPO:
            img = CDN.format(repo=REPO, branch=BRANCH, name=fname)
            desc = " · ".join(x for x in [d["addr"], f"마감 {d['deadline']}"] if x)
            items = [(t["name"][:6], f"{t['area'].replace('전용 ','')} {t['deposit']}")
                     for t in d["types"] if t["deposit"] != "—"]
            kk.send_feed(title=f"{d['name']} {d['dday']}", desc=desc,
                         image_url=img, link=n["url"],
                         button="공고 보기", items=items)
            sent += 1

    # 3) 상태 저장
    soco_ids = [n["board_id"] for n in news if n.get("board_id")]
    if soco_ids and not any("청년안심주택 수집 실패" in e for e in errors):
        state["soco_last_board_id"] = max(soco_ids)
    if "_hillstate_count_new" in state:
        state["hillstate_count"] = state.pop("_hillstate_count_new")
    for sl, v in state.pop("_space_new", {}).items():
        state["spacereits"][sl] = v
    state["checked"] = str(today)
    save_state(state)

    # 4) 리프레시 토큰 회전
    if kk and kk.new_refresh_token:
        ok = update_github_secret(kk.new_refresh_token)
        msg = ("카카오 리프레시 토큰이 갱신되었습니다." if ok else
               "⚠ 카카오 리프레시 토큰이 새로 발급되었으나 자동 저장에 실패했습니다.\n"
               "GitHub Secrets 의 KAKAO_REFRESH_TOKEN 을 직접 갱신해주세요.")
        print(msg)
        if not ok and kk:
            kk.send_text(msg)

    for e in errors:
        print("ERROR:", e, file=sys.stderr)
    print(f"\n신규 {len(news)}건 / 카드 발송 {sent}건 / dry_run={dry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
