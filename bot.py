#!/usr/bin/env python3
"""서울 공공지원민간임대·청년안심주택 신규 공고 → 슬랙 알림 (카카오톡은 KAKAO_ENABLED 로 켜고 끔).

GitHub Actions 에서 평일 07:47 KST 에 실행된다.
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
from slack import Slack

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state.json"
CARDS = ROOT / "cards"
KST = dt.timezone(dt.timedelta(hours=9))

CARD_LIMIT = int(os.environ.get("CARD_LIMIT", "3"))   # 카드로 보낼 최대 건수
REPO = os.environ.get("GITHUB_REPOSITORY", "")        # owner/name
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
CDN = "https://cdn.jsdelivr.net/gh/{repo}@{branch}/cards/{name}"

URGENT_DAYS = int(os.environ.get("URGENT_DAYS", "3"))  # 마감 N일 이내면 마감임박

DEFAULT_STATE = {
    "soco_last_board_id": 6645,
    "hillstate_count": 28,
    "spacereits": {k: "" for k in S.SPACEREITS},
    "tracking": [],      # 접수 진행 중인 공고 — 신규가 아니어도 마감을 계속 본다
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
    """작을수록 먼저. 마감임박 > 최초모집 > 회전빠른단지 > 추가모집 > 관심단지 공지

    이전에는 hillstate/spacereits 를 1순위에 둬서, 모집도 아닌 공지 3건이
    CARD_LIMIT 을 다 먹고 진짜 신규 모집공고 카드가 안 나가는 일이 있었다.
    """
    if n.get("urgent"):
        return 0
    t = n.get("title", "")
    if n["source"] == "soco":
        if "최초" in t:
            return 1
        if any(k in t for k in S.FAST_TURNOVER):
            return 2
        return 3
    return 4 if n.get("is_recruit") else 5


# ─────────────────────────── 수집 ───────────────────────────

def collect(state: dict, today: dt.date):
    news: list[dict] = []
    errors: list[str] = []
    open_now: list[dict] = []        # 접수 진행 중(신규 여부와 무관)

    # (A) 청년안심주택 — 최근 2페이지를 통째로 읽는다.
    #     신규 판정은 board_id 로 하되, 접수가 열려 있는 공고는 '이미 알던 것'이라도
    #     마감 추적 목록에 넣는다. (신규만 보던 예전 방식은 어제 알린 공고가
    #     오늘 마감인 경우를 놓쳤다.)
    last_id = state["soco_last_board_id"]
    try:
        fetched = S.fetch_soco(0, max_pages=2)
        if fetched:
            state["_soco_max_seen"] = max(x["board_id"] for x in fetched)
        for n in fetched:
            left = S.dday_days(n["deadline"], today)
            n["days_left"] = left
            n["dday"] = S.dday(n["deadline"], today)
            n["urgent"] = left is not None and 0 <= left <= URGENT_DAYS
            n["is_recruit"] = True

            if left is not None and left >= 0:
                open_now.append({
                    "board_id": n.get("board_id"), "name": clean_name(n["title"]),
                    "deadline": n["deadline"], "url": n["url"],
                    "addr": n.get("addr", ""),
                    "title": n.get("title", ""), "pdf": n.get("pdf"),
                    "apply_url": _apply_url(n),
                })
            if n["board_id"] <= last_id:
                continue                       # 이미 알린 공고
            if left is not None and left < 0:
                # 봇이 하루 걸렀을 때 이미 끝난 공고를 신규로 알리지 않는다
                print(f"skip(마감됨): {n['title'][:50]} — {n['deadline']}")
                continue
            news.append(n)
    except Exception as e:
        errors.append(f"청년안심주택 수집 실패: {e}")

    # (B) 힐스테이트 관악 뉴포레 — 글 수가 늘었을 때만, 모집공고일 때만 알림
    try:
        h = S.fetch_hillstate()
        if h["count"] > state["hillstate_count"] and h["is_recruit"]:
            news.append({
                "source": "hillstate", "title": f"힐스테이트 관악 뉴포레 — {h['latest']}",
                "name": "힐스테이트 관악 뉴포레", "kicker": "예비임차인 모집 · 공공지원민간임대",
                "addr": "서울 관악구 조원로 25", "station": "신림동",
                "supply": "1,143세대 · 전용 44/59/84㎡", "operator": "서울리츠4호 · KT리빙",
                "deadline": "", "dday": "", "urgent": False, "is_recruit": True,
                "types": [], "url": h["url"], "site": "hillstatenewfore.co.kr", "pdf": None,
            })
        state["_hillstate_count_new"] = h["count"]
    except Exception as e:
        errors.append(f"힐스테이트 뉴포레 확인 실패: {e}")

    # (C) 공간지원리츠 — 제목+작성일이 바뀌고, 그게 모집공고일 때만 알림
    for sl, name in S.SPACEREITS.items():
        r = S.fetch_spacereits(sl)
        if r.get("error"):
            errors.append(f"{name} 확인 실패")
            continue
        prev = state["spacereits"].get(sl, "")
        changed = bool(r["key"]) and r["key"] != prev
        if changed and r["is_recruit"]:
            news.append({
                "source": "spacereits", "title": f"{name} — {r['title'][:60]}",
                "name": name, "kicker": "임차인 모집 · 공공지원민간임대",
                "addr": name, "station": "", "supply": "", "operator": "공간지원리츠",
                "deadline": "", "dday": "", "urgent": False, "is_recruit": True,
                "types": [], "url": r["url"], "site": "", "pdf": None,
            })
        elif changed:
            # 추첨결과·등기·안내문 같은 후속 공지는 조용히 상태만 갱신한다
            print(f"skip(모집 아님): {name} — {r['title'][:50]}")
        state.setdefault("_space_new", {})[sl] = r["key"]

    news.sort(key=priority)
    return news, errors, open_now


def refresh_tracking(state: dict, open_now: list[dict], today: dt.date) -> list[dict]:
    """접수 중인 공고를 state 에 쌓아두고, 매 실행마다 D-day 를 다시 센다.

    신규 공고만 보던 예전 방식은 '어제 이미 알린 공고가 오늘 마감'인 경우를
    통째로 놓쳤다(천호한강 9/9 마감을 마감임박 0건으로 보고한 건).
    """
    keep: dict = {}
    for row in list(state.get("tracking", [])) + open_now:
        key = str(row.get("board_id") or row.get("url"))
        keep[key] = {**keep.get(key, {}), **row}

    alive, urgent = [], []
    for row in keep.values():
        left = S.dday_days(row.get("deadline", ""), today)
        if left is None or left < 0:
            continue                      # 마감된 건 목록에서 뺀다
        row["days_left"] = left
        row["dday"] = S.dday(row["deadline"], today)
        alive.append(row)
        if left <= URGENT_DAYS:
            urgent.append(row)
    alive.sort(key=lambda r: r["days_left"])
    urgent.sort(key=lambda r: r["days_left"])
    state["tracking"] = alive
    return urgent


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

def _apply_url(n: dict) -> str:
    """신청 버튼 주소. 공고 본문의 '청약신청 페이지' 줄(sources.parse_body)에 적힌 주소만 인정한다."""
    u = (n.get("apply_url") or "").strip()
    return u if u and len(u) <= 3000 and "soco.seoul.go.kr" not in u else ""


def slack_item(n: dict, today: dt.date, fetch_pdf: bool = True) -> dict:
    """공고 1건 → 슬랙 카드 1개에 들어갈 값. 모르는 값은 지어내지 않고 '공고문 확인'."""
    title = n.get("title", "")
    name = n.get("name") or clean_name(title) or "공고"
    left = n.get("days_left")
    if left is None and n.get("deadline"):
        left = S.dday_days(n["deadline"], today)
    dd = n.get("dday") or (S.dday(n["deadline"], today) if n.get("deadline") else "")
    urgent = left is not None and 0 <= left <= URGENT_DAYS
    head = f"⚠ 마감 {dd} · {name}" if urgent and dd else name

    types = n.get("types") or []
    if not types and fetch_pdf and n.get("pdf"):
        try:
            types = fetch_types(n["pdf"])
        except Exception as e:
            print(f"slack: PDF 읽기 실패 {n.get('board_id')} ({type(e).__name__})")
            types = []
    if types:
        t = types[0]
        summary = f"{t['area']} · 보증금 {t['deposit']}만원 · 월세 {t['rent']}만원"
        if len(types) > 1:
            summary += f" (외 {len(types) - 1}개 타입)"
    else:
        summary = "전용면적·보증금·월세: 공고문 확인"

    kind = kind_of(title) if title else "모집공고"
    qual = "자격 공고문 확인"                # 자격은 공고마다 달라 읽은 값이 없으면 지어내지 않는다
    meta = f"마감 {n.get('deadline') or '공고문 확인'} · {kind} · {qual}"
    return {"title": head, "summary": summary, "meta": meta, "urgent": urgent,
            "notice_url": n["url"], "apply_url": _apply_url(n)}


def slack_test() -> int:
    """수동 실행 '슬랙 시험': 최근 실제 공고 1건으로 카드 견본 1개만 보낸다.
    상태 파일·카톡은 건드리지 않는다."""
    slk = Slack.from_env()
    if not slk:
        print("slack test: SLACK_WEBHOOK_URL 이 없습니다")
        return 1
    today = dt.datetime.now(KST).date()
    try:
        latest = S.fetch_soco(0, max_pages=1)[0]
        item = slack_item(latest, today)
    except Exception as e:
        print(f"slack test: 견본 공고 읽기 실패 ({type(e).__name__})")
        slk.send_text("🧪 seoul-notice-bot 슬랙 시험: 견본 공고를 읽지 못했습니다(연결만 확인됨)")
        return 1
    sent, failed = slk.send_notices(
        [item], header="🧪 슬랙 카드 견본 · 최근 실제 공고 1건 (시험 실행, 상태 파일 변경 없음)")
    print(f"slack test: {'ok' if sent and not failed else 'fail'} (board_id {latest.get('board_id')})")
    return 0 if sent and not failed else 1


def main() -> int:
    if os.environ.get("SLACK_TEST") == "1":
        if os.environ.get("DRY_RUN") == "1":
            print("slack test: dry_run=1 이라 보내지 않음")
            return 0
        return slack_test()

    today = dt.datetime.now(KST).date()
    state = load_state()
    news, errors, open_now = collect(state, today)
    urgent_open = refresh_tracking(state, open_now, today)

    rest_key = os.environ.get("KAKAO_REST_KEY", "")
    refresh = os.environ.get("KAKAO_REFRESH_TOKEN", "")
    secret  = os.environ.get("KAKAO_CLIENT_SECRET", "")
    # 카톡 스위치: KAKAO_ENABLED=0 이면 카톡 발송·토큰 갱신을 하지 않는다(코드는 그대로 둠).
    # 되살리려면 워크플로의 KAKAO_ENABLED 를 "1" 로 바꾼다.
    kakao_on = os.environ.get("KAKAO_ENABLED", "1") == "1" and bool(rest_key and refresh)
    slk = Slack.from_env()
    dry = os.environ.get("DRY_RUN") == "1" or not (kakao_on or slk)

    kk = None
    if not dry and kakao_on:
        kk = Kakao(rest_key, refresh, secret)
        kk.refresh()
    # 슬랙: 카톡과 같은 내용(신규는 전부). 드라이런이면 보내지 않는다.
    if dry:
        slk = None
    print(f"channels: kakao={'on' if kk else 'off'} slack={'on' if slk else 'off'}")
    if not kk and not slk and os.environ.get("DRY_RUN") != "1":
        print("⚠ 보낼 채널이 없습니다(카톡 꺼짐 + SLACK_WEBHOOK_URL 없음) — 상태 저장 없이 종료")
        return 1

    stamp = today.strftime("%-m/%-d")

    # 1) 헤더 — 신규 목록과 마감임박 목록을 링크까지 같이 적는다. (카톡)
    #    카드 이미지가 안 뜨거나 CARD_LIMIT 에 밀려도 최소한 링크는 남게.
    blocks: list[tuple[str, str]] = []   # (본문, 링크)
    if news or urgent_open:
        summary = (f"{stamp} 서울 임대주택\n"
                   f"신규 {len(news)}건 · 마감임박 {len(urgent_open)}건")
    else:
        summary = (f"{stamp} 확인 완료\n신규·마감임박 없음\n"
                   f"청년안심 {state['soco_last_board_id']} · 뉴포레 {state['hillstate_count']}건")
    if errors:
        summary += f"\n⚠ 확인 실패 {len(errors)}건"
    blocks.append((summary, "https://soco.seoul.go.kr/youth/main/main.do"))

    for r in urgent_open[:5]:
        blocks.append((f"⚠ 마감임박 {r['dday']} · {r['name']}\n"
                       f"마감 {r['deadline']}\n{r.get('addr','')}\n{r['url']}", r["url"]))
    shown = {str(r.get("board_id") or r.get("url")) for r in urgent_open[:5]}
    for n in news[:5]:
        if str(n.get("board_id") or n.get("url")) in shown:
            continue                     # 마감임박으로 이미 위에 올린 건 두 번 쓰지 않는다
        nm = n.get("name") or clean_name(n["title"])
        blocks.append((f"🆕 {nm}\n마감 {n.get('deadline') or '공고문 확인'}\n"
                       f"{n.get('addr','')}\n{n['url']}", n["url"]))

    if kk:
        for body, link in blocks:
            print(body, "\n---")
            kk.send_text(body, link=link)

    # 2) 카드 (카톡) — 카톡이 꺼져 있으면 카드 이미지도 만들지 않는다
    sent = 0
    if kk:
        CARDS.mkdir(exist_ok=True)
        for n in news[:CARD_LIMIT]:
            d = card_data(n, today)
            fname = f"{today:%Y%m%d}-{slug(d['name'])}.png"
            path = CARDS / fname
            asyncio.run(render(d, str(path)))
            print("card:", fname)

            if REPO:
                img = CDN.format(repo=REPO, branch=BRANCH, name=fname)
                dep = next((t["deposit"] for t in d["types"] if t["deposit"] != "—"), "")
                desc = " · ".join(x for x in [
                    d["addr"], d["supply"], f"마감 {d['deadline']}",
                    f"보증금 {dep}만원" if dep else "", d["url"]] if x)
                items = [(t["name"][:6], f"{t['area'].replace('전용 ','')} {t['deposit']}")
                         for t in d["types"] if t["deposit"] != "—"]
                kk.send_feed(title=f"{d['name']} {d['dday']}", desc=desc,
                             image_url=img, link=n["url"],
                             button="공고 보기", items=items)
                sent += 1

    # 2-b) 슬랙 — 공고 1건 = 카드 1개. 마감임박을 먼저, 신규는 전부.
    slack_msgs = slack_failed = 0
    if slk:
        cards: list[dict] = []
        seen: set[str] = set()
        by_key = {str(n.get("board_id") or n.get("url")): n for n in news}
        for r in urgent_open:
            key = str(r.get("board_id") or r.get("url"))
            seen.add(key)
            if key in by_key:            # 신규이면서 마감임박이면 신규 쪽 자세한 값(평형·PDF)을 쓴다
                cards.append(slack_item({**r, **by_key[key]}, today))
            else:
                cards.append(slack_item(r, today))
        for n in news:
            key = str(n.get("board_id") or n.get("url"))
            if key in seen:
                continue
            seen.add(key)
            cards.append(slack_item(n, today))
        n_urgent = sum(1 for c in cards if c["urgent"])
        if len(cards) > 1:
            header = f"🏠 신규 {len(news)}건 · 마감임박 {n_urgent}건"
        elif not cards and errors:
            header = f"⚠ {stamp} 확인 일부 실패 · 확인된 곳에는 신규·마감임박 없음"
        elif not cards:
            header = f"✅ {stamp} 확인 완료 · 신규·마감임박 없음"
        else:
            header = ""
        footer = f"⚠ 확인 실패 {len(errors)}건: " + ", ".join(e[:40] for e in errors) if errors else ""
        slack_msgs, slack_failed = slk.send_notices(cards, header=header, footer=footer)
        print(f"slack: 카드 {len(cards)}개 / 메시지 {slack_msgs}건 성공 / {slack_failed}건 실패")
        if slack_failed and not kk:
            # 슬랙이 유일한 채널인데 실패하면 상태를 저장하지 않는다 → 다음 실행에서 다시 보낸다
            print("⚠ 슬랙 전송 실패 — 상태 저장 없이 종료(다음 실행에서 재발송)")
            return 1

    # 3) 상태 저장
    # 마감돼서 건너뛴 공고도 '본 것'이므로 최댓값으로 올린다 (매일 재평가 방지)
    seen_max = state.pop("_soco_max_seen", 0)
    if seen_max and not any("청년안심주택 수집 실패" in e for e in errors):
        state["soco_last_board_id"] = max(seen_max, state["soco_last_board_id"])
    if "_hillstate_count_new" in state:
        state["hillstate_count"] = state.pop("_hillstate_count_new")
    for sl, v in state.pop("_space_new", {}).items():
        state["spacereits"][sl] = v
    state["checked"] = str(today)
    if os.environ.get("DRY_RUN") == "1":
        print("dry_run: 상태 파일 저장 안 함")
    else:
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
    print(f"\n신규 {len(news)}건 / 카톡 카드 {sent}건 / 슬랙 메시지 {slack_msgs}건(실패 {slack_failed}) / dry_run={dry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
