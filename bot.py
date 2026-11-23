#!/usr/bin/env python3
"""서울 공공지원민간임대·청년안심주택 신규 공고 → 슬랙 알림 (카카오톡은 KAKAO_ENABLED 로 켜고 끔).

GitHub Actions 에서 매일 07:47 KST + 금요일 18:17 KST(추가 실행)에 예약된다(실제 시작은 GitHub 대기열로 몇 시간 늦을 수 있음).
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
import myhome as MH
import elyes_ocr as OCR
from pdfparse import fetch_types, type_lines, fmt_num
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
SPACEREITS_ENABLED = os.environ.get("SPACEREITS_ENABLED", "0") == "1"  # 공간지원리츠는 맥북에서 확인(해외 IP 차단)
# 추가 실행(금요일 저녁): 새 공고만 보낸다 — 마감임박 재알림·'신규 없음' 줄은 아침 실행에서만
QUIET = os.environ.get("RUN_KIND", "") == "extra"
WD = "월화수목금토일"

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

    # (B) 힐스테이트 관악 뉴포레 — 처음 보는 글(등록일|제목) 중 모집공고만 알림.
    #     글 수만 비교하면 여러 글이 한꺼번에 올라오거나 글이 바뀔 때 모집 글을 놓친다.
    try:
        h = S.fetch_hillstate()
        if not h["rows"]:
            raise ValueError("글 목록 0건")
        seen = state.get("hillstate_seen")
        keys = [x["key"] for x in h["rows"]]
        if seen is None:
            print(f"hillstate: 처음 실행 — 현재 글 {len(keys)}개를 본 것으로 기록(알림 없음)")
        else:
            for x in h["rows"]:
                if x["key"] in seen:
                    continue
                if not x["is_recruit"]:
                    print(f"skip(모집 아님): 힐스테이트 — {x['title'][:50]}")
                    continue
                news.append({
                    "board_id": f"hill:{x['key']}",            # 같은 게시판 URL 이라 글마다 따로 구분
                    "source": "hillstate", "title": f"힐스테이트 관악 뉴포레 — {x['title']}",
                    "name": f"힐스테이트 관악 뉴포레 — {x['title'][:30]}", "kicker": "예비임차인 모집 · 공공지원민간임대",
                    "addr": "서울 관악구 조원로 25", "station": "신림동",
                    "supply": "", "operator": "",           # 공고마다 달라 고정값을 넣지 않는다
                    "deadline": "", "dday": "", "urgent": False, "is_recruit": True,
                    "types": [], "url": h["url"], "site": "hillstatenewfore.co.kr", "pdf": None,
                })
        state["_hillstate_seen_new"] = (keys + [k for k in (seen or []) if k not in keys])[:max(200, len(keys))]
        state["_hillstate_count_new"] = h["count"]
    except Exception as e:
        errors.append(f"힐스테이트 뉴포레 확인 실패: {e}")

    # (C) 공간지원리츠 — 제목+작성일이 바뀌고, 그게 모집공고일 때만 알림
    # 2026-11-19부터 꺼 둠: spacereits.co.kr 이 GitHub Actions(해외) IP 를 접속 단계에서 끊는다.
    # 맥북 galchi-automations/housing-local/spacereits.py 가 평일 08:10 에 대신 확인한다.
    # 되살리려면 워크플로 env SPACEREITS_ENABLED: "1".
    for i, (sl, name) in enumerate(S.SPACEREITS.items() if SPACEREITS_ENABLED else []):
        r = S.fetch_spacereits(sl)
        if r.get("error"):
            print(f"공간지원리츠: {name} 실패 — {r.get('why')}")
            if r.get("unreachable"):
                # 다섯 단지가 한 서버라, 접속이 안 되면 나머지도 안 된다 — 기다리지 않고 건너뛴다
                rest = len(S.SPACEREITS) - i - 1
                print(f"공간지원리츠: 접속 안 됨 — 나머지 {rest}곳 건너뜀(상태는 그대로, 다음 실행에서 다시 확인)")
                errors.append(f"공간지원리츠 접속 안 됨({r.get('why')})")
                break
            errors.append(f"{name} 확인 실패({r.get('why')})")
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

    # (D) SH 주택임대 공고 · (E) 롯데 엘리스 모집공고 — 처음 보는 글 중 조건에 맞는 모집공고만
    def board(label, key, rows, mk):
        seen = state.get(key)
        ids = [str(x["id"]) for x in rows]
        if seen is None:
            print(f"{label}: 처음 실행 — 현재 글 {len(ids)}개를 본 것으로 기록(알림 없음)")
        else:
            for x in rows:
                if str(x["id"]) in seen:
                    continue
                if not x["mine"]:
                    print(f"skip(조건 밖·모집 아님): {label} — {x['title'][:50]}")
                    continue
                news.append(mk(x))
        # 지금 목록에 보이는 글은 절대 잘라내지 않는다(고정 공지가 빠지면 매일 다시 알리게 됨)
        state["_new_" + key] = (ids + [x for x in (seen or []) if x not in ids])[:max(300, len(ids))]

    try:
        sh = [{**x, "id": x["seq"]} for x in S.fetch_sh()]
        if not sh:
            raise ValueError("글 목록 0건")
        board("SH", "sh_seen", sh, lambda x: {
            "board_id": f"sh:{x['id']}", "source": "sh", "title": f"SH — {x['title']}", "name": x["title"][:40],
            "raw_title": x["title"],
            "kicker": "SH 주택임대 공고", "addr": "", "deadline": "", "dday": "", "urgent": False,
            "is_recruit": True, "types": [], "url": x["url"], "site": "", "pdf": None})
    except Exception as e:
        errors.append(f"SH 주택임대 확인 실패: {type(e).__name__}")

    try:
        # 지역 기준이 '서울'에서 '서울+근교 7곳'으로 바뀌어(2026-11-19) 판정을 새로 저장한다(elyes_regions2)
        regions = S.elyes_regions(state.get("elyes_regions2", {}))
        state["_new_elyes_regions2"] = regions
        el = S.fetch_elyes(regions)
        if not el:
            raise ValueError("글 목록 0건")
        board("엘리스", "elyes_seen", el, lambda x: {
            "board_id": f"elyes:{x['id']}", "source": "elyes", "title": f"롯데 엘리스 — {x['title']}"
                                        + (" (지역 미확인)" if x["region"] == "미확인" else ""),
            "name": x["title"][:40], "raw_title": x["title"], "kicker": "롯데 엘리스 모집공고", "addr": "",
            "deadline": "", "dday": "", "urgent": False, "is_recruit": True, "types": [],
            "url": x["url"], "site": "", "pdf": None, "images": x.get("images", []), "posted": x.get("date", "")})
        now = dt.datetime.now(KST).replace(tzinfo=None)
        if now.date() != today:                       # 시험에서 today 를 바꾼 경우
            now = dt.datetime.combine(today, dt.time(12, 0))
        news[:] = [e for e in (elyes_enrich(n, now) if n.get("source") == "elyes" else n for n in news) if e]
    except Exception as e:
        errors.append(f"롯데 엘리스 확인 실패: {type(e).__name__}")

    # (F) 마이홈 공공데이터 API — 서울+근교 7곳 공공임대 모집공고. 면적 정보 없음(📐 표시)
    if os.environ.get("MYHOME_API_KEY", "").strip():
        try:
            mh = MH.fetch_notices(today)
            board("마이홈", "myhome_seen", [{**x, "mine": S.mine(x["title"])} for x in mh], lambda x: {
                "board_id": f"myhome:{x['id']}", "source": "myhome", "title": f"마이홈 — {x['title']}",
                "name": x["title"][:40], "raw_title": x["title"], "kicker": f"{x['insttNm']} {x['suplyTyNm']}",
                "addr": x["region"], "deadline": x["deadline"] or "공고문 확인", "days_left": x["days_left"],
                "dday": ("D-DAY" if x["days_left"] == 0 else f"D-{x['days_left']}") if x["days_left"] is not None else "",
                "urgent": x["days_left"] is not None and 0 <= x["days_left"] <= URGENT_DAYS,
                "is_recruit": True, "types": x["types"], "types_complete": True,
                "url": x["url"], "apply_url": x["url"], "site": "", "pdf": None})
        except Exception as e:
            errors.append(f"마이홈 API 확인 실패: {MH.redact(str(e))[:60]}")
    else:
        print("myhome: MYHOME_API_KEY 없음 — 건너뜀")

    news = dedupe(news, state)
    news.sort(key=priority)
    return news, errors, open_now


def tnorm(t: str) -> str:
    """제목 비교용: 한글·영문·숫자만, '공고문'→'공고'."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", (t or "").replace("공고문", "공고"))


def dedupe(news: list[dict], state: dict) -> list[dict]:
    """같은 공고가 마이홈과 다른 출처에 함께 오면 한 번만 알린다. 제목(정규화)이 정확히 같을 때만 같은 공고로 본다
    ('[정정]' 같은 앞뒤 글자가 붙은 새 공고를 빼지 않으려고 부분 일치는 쓰지 않는다).
    - 같은 실행: 마이홈 쪽을 빼고, 남는 공고에 없는 마감 정보를 마이홈에서 옮긴다.
    - 이전 실행: 다른 출처로 먼저 알린 제목(alerted_titles)이면 마이홈을 빼고,
      마이홈으로 먼저 알린 제목(myhome_alerted)이면 SH·엘리스 쪽을 뺀다.
      청년안심주택(soco)은 빼지 않는다('추가모집공고'가 같은 제목으로 매달 새로 올라온다)."""
    before = set(state.get("alerted_titles", []))
    mh_before = set(state.get("myhome_alerted", []))
    key = lambda n: tnorm(n.get("raw_title") or n.get("title"))
    others = {key(n): n for n in news if n.get("source") != "myhome" and len(key(n)) >= 10}
    keep, mh_sent = [], []
    for n in news:
        k = key(n)
        if n.get("source") == "myhome" and len(k) >= 10:
            twin = others.get(k)
            if twin is not None:
                for f in ("deadline", "days_left", "dday", "urgent"):
                    if not twin.get(f) and n.get(f):
                        twin[f] = n[f]                    # 마감 정보는 살린다
                print(f"skip(다른 출처와 중복·합침): 마이홈 — {(n.get('raw_title') or n['title'])[:50]}")
                continue
            if k in before:
                print(f"skip(이미 다른 출처로 알림): 마이홈 — {(n.get('raw_title') or n['title'])[:50]}")
                continue
            mh_sent.append(k)
        elif n.get("source") in ("sh", "elyes") and k in mh_before:
            print(f"skip(이미 마이홈으로 알림): {n.get('source')} — {(n.get('raw_title') or n['title'])[:50]}")
            continue
        keep.append(n)
    state["_new_alerted_titles"] = (list(others) + [o for o in state.get("alerted_titles", []) if o not in others])[:500]
    state["_new_myhome_alerted"] = (mh_sent + [o for o in state.get("myhome_alerted", []) if o not in mh_sent])[:500]
    return keep


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
    def _rng(vals):
        lo, hi = min(vals), max(vals)
        return fmt_num(lo) if lo == hi else f"{fmt_num(lo)}~{fmt_num(hi)}"
    rows = [{"name": t["label"] or "타입",
             "area": f"전용 {t['area']}㎡" if t["area"] else "전용면적 확인 실패",
             "deposit": _rng([p["deposit"] for p in t["prices"]]),
             "rent": _rng([p["rent"] for p in t["prices"]])} for t in types[:4]]
    if not rows:
        rows = [{"name": "평형별 조건", "area": "확인 실패",
                 "deposit": "—", "rent": "—"}]

    # 자격은 공고마다 달라 지어내지 않는다(예전엔 '만 19~39세 무주택' 등을 고정으로 넣었음)
    quals = ["자격 조건: 공고문 확인"]

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


MIN_AREA = float(os.environ.get("MIN_AREA", "30"))   # 전용 이 값(㎡) 미만 타입은 표에서 뺀다


def short_kind(kind: str) -> str:
    """'청년 또는 신혼 부부 특별' → '청년·신혼 특별' (표 칸이 좁아 짧게)."""
    k = kind or ""
    y, h = "청년" in k, "신혼" in k
    base = "청년·신혼" if y and h else "청년" if y else "신혼" if h else ""
    sup = "특별" if "특별" in k else "일반" if "일반" in k else ""
    return " ".join(x for x in (base, sup) if x) or k[:10]


def _area_val(t: dict) -> float | None:
    try:
        return float(t["area"]) if t.get("area") else None
    except ValueError:
        return None


# ─────────────────────────── 롯데 엘리스 ───────────────────────────

def _md(d: dt.datetime, with_time: bool = True) -> str:
    s = f"{d.month}/{d.day}({WD[d.weekday()]})"
    return s + (f" {d:%H:%M}" if with_time and d.time() not in (dt.time(0, 0), dt.time(23, 59)) else "")


def elyes_enrich(n: dict, now: dt.datetime, images: list[bytes] | None = None) -> dict | None:
    """엘리스 공고 1건: 공고문 이미지를 읽어 임대조건·일정을 채운다. 신청 마감이 지났으면 None(보내지 않음).
    마감은 공고문 표 → 제목 '(접수기간: …)' 순으로 찾고, 둘 다 없으면 '마감일 확인 필요'로 보낸다."""
    title = n.get("raw_title") or n.get("title", "")
    td = S.elyes_title_deadline(title, n.get("posted", ""))
    if images is None:
        images = S.elyes_images(n.get("images") or [])
    r = OCR.read_images(images) if images else {"rows": [], "apply": (None, None), "winner": None,
                                                 "contract": (None, None), "errors": ["공고문 이미지 없음"]}
    for e in r["errors"]:
        print(f"elyes: {title[:30]} — {e}")
    end = r["apply"][1]
    if end and td and end.date() != td[0]:
        print(f"elyes: 제목 마감 {td[0]} 와 공고문 {end:%Y-%m-%d} 가 달라 제목 날짜를 씀 — {title[:40]}")
        end = None
    if end is None and td:
        hh = td[1] if td[1] is not None and td[1] < 24 else None
        end = dt.datetime.combine(td[0], dt.time(hh, 0) if hh is not None else dt.time(23, 59))
    if end and end < now:
        print(f"skip(마감됨): 엘리스 — {title[:50]} — 마감 {end:%m/%d %H:%M}")
        return None
    rows = r["rows"] if r["rows"] and all(OCR.plausible(x) for x in r["rows"]) else []
    m = dict(n)
    m["ely_rows"] = rows
    m["ely_sched"] = {"apply": r["apply"] if r["apply"][1] else (None, end), "winner": r["winner"],
                      "contract": r["contract"]}
    if end:
        left = (end.date() - now.date()).days
        m.update(deadline=_md(end), days_left=left, dday="D-DAY" if left == 0 else f"D-{left}",
                 urgent=0 <= left <= URGENT_DAYS)
    else:
        m.update(deadline="", days_left=None, dday="", urgent=False)
    return m


def _man(won: int) -> str:
    """원 → 만원 문자열: 10000000 → '1,000' · 628000 → '62.8'."""
    return f"{won / 10000:,.1f}".rstrip("0").rstrip(".")


def _ely_sched_text(sc: dict) -> str:
    a0, a1 = sc.get("apply") or (None, None)
    c0, c1 = sc.get("contract") or (None, None)
    parts = []
    if a1:
        parts.append("신청 " + (f"{_md(a0)}~" if a0 else "") + (
            f"{a1:%H:%M}" if a0 and a0.date() == a1.date() else _md(a1)))
    if sc.get("winner"):
        parts.append(f"발표 {_md(sc['winner'])}")
    if c1:
        parts.append("계약 " + (f"{_md(c0, False)}~" if c0 else "") + _md(c1, False))
    return " · ".join(parts)


def elyes_slack_item(n: dict) -> dict:
    """엘리스 공고 카드. 표: 타입 · 보증금 · 월세 (+ 공고문에 있는 층·향·용도·세대수). 못 읽은 값은 '공고문 확인'."""
    raw = n.get("raw_title") or n.get("name") or ""
    name = re.sub(r"\s*[(（][^)）]*[)）][^\]]*$", "", raw)            # 끝의 '(접수기간: …)' · '(공고일: …)' 떼기
    name = re.sub(r"\s+", " ", re.sub(r"\[([^\]]+)\]", r"\1 ", name)).strip()[:40] or "엘리스 공고"
    left, dd = n.get("days_left"), n.get("dday", "")
    urgent = left is not None and 0 <= left <= URGENT_DAYS
    top = (f"⚠️ {dd} · " if urgent and dd else "") + f"{name} · 마감 {n.get('deadline') or '확인 필요'}"
    flags = [] if n.get("deadline") else ["⏰ 마감일 확인 필요"]
    allr = n.get("ely_rows") or []
    # 전용면적이 적힌 공고(예: 어바니엘 염창)는 다른 출처와 같은 기준으로 MIN_AREA㎡ 미만 타입을 뺀다
    src = [r for r in allr if not r.get("area") or float(r["area"]) >= MIN_AREA]
    skip = bool(allr) and not src
    dropped = len(allr) - len(src)
    if not src or any(not r.get("area") for r in src):
        flags.append("📐 면적 확인 필요")
    sched = _ely_sched_text(n.get("ely_sched") or {})
    if src:
        opt = [(k, h) for k, h in (("area", "전용㎡"), ("floor", "층"), ("dir", "향"), ("use", "용도"), ("units", "세대수"))
               if any(r.get(k) for r in src)]
        head = ["타입", "보증금(만원)", "월세(만원)"] + [h for _, h in opt]
        rows = [[r["type"] or "확인 실패", _man(r["deposit"]), _man(r["rent"])] + [r.get(k) or "-" for k, _ in opt]
                for r in src]
        fallback = "\n".join(" · ".join(x for x in [
            r["type"] or "타입 확인 실패", f"보증금 {_man(r['deposit'])}만", f"월 {_man(r['rent'])}만",
            " ".join(x for x in (r.get("floor"), r.get("dir"), r.get("use")) if x)] if x) for r in src)
    else:
        head, rows, fallback = None, None, "타입·보증금·월세: 공고문 확인"
    if sched:
        fallback += "\n" + sched
    notes = sorted({r["note"] for r in src if r.get("note")})
    note = " · ".join(x for x in [
        "단위: 만원" if rows else "", sched or "신청 일정: 공고문 확인",
        ("비고 " + ", ".join(notes)) if notes else "",
        f"전용 {MIN_AREA:g}㎡ 미만 {dropped}개 타입 제외" if dropped and not skip else "",
        "자격·입주조건 공고문 확인"] if x)
    return {"top": top, "flags": flags, "rows": rows, "head": head, "money_cols": [1, 2],
            "caption": f"{name} 임대조건", "note": note, "fallback_text": fallback,
            "urgent": urgent, "skip": skip, "dropped_types": dropped,
            "notice_url": n["url"], "apply_url": "",
            "title": top, "summary": fallback, "meta": note}


def slack_item(n: dict, today: dt.date, fetch_pdf: bool = True) -> dict:
    """공고 1건 → 슬랙 메시지 1개에 들어갈 값. 모르는 값은 지어내지 않고 '확인 실패'·'공고문 확인'.
    전용 MIN_AREA㎡ 미만 타입은 표에서 빼고, 모든 타입이 미만이면 skip=True(보내지 않음)."""
    if n.get("source") == "elyes" and "ely_rows" in n:
        return elyes_slack_item(n)
    title = n.get("title", "")
    name = n.get("name") or clean_name(title) or "공고"
    left = n.get("days_left")
    if left is None and n.get("deadline"):
        left = S.dday_days(n["deadline"], today)
    dd = n.get("dday") or (S.dday(n["deadline"], today) if n.get("deadline") else "")
    urgent = left is not None and 0 <= left <= URGENT_DAYS
    deadline = n.get("deadline") or "공고문 확인"
    top = (f"⚠️ {dd} · " if urgent and dd else "") + f"{name} · 마감 {deadline}"

    types = n.get("types") or []
    complete = n.get("types_complete", True)
    if not types and fetch_pdf and n.get("pdf"):
        try:
            types, complete = fetch_types(n["pdf"], with_status=True)
        except Exception as e:
            print(f"slack: PDF 읽기 실패 {n.get('board_id')} ({type(e).__name__})")
            types, complete = [], False

    known = [t for t in types if _area_val(t) is not None]
    kept = [t for t in types if _area_val(t) is None or _area_val(t) >= MIN_AREA]
    # 모든 타입이 면적 미만일 때만 뺀다. 표 일부를 못 읽었으면(못 읽은 쪽에 큰 타입이 있을 수 있음) 빼지 않는다
    skip = bool(types) and complete and len(known) == len(types) and not kept
    area_unknown = (not types) or (not complete) or any(_area_val(t) is None for t in kept)

    if kept:
        rows = [[t["label"] or "확인 실패", t["area"] or "확인 실패", short_kind(t["kind"]), p["ratio"],
                 fmt_num(p["deposit"]), fmt_num(p["rent"])] for t in kept for p in t["prices"]]
        fallback = type_lines(kept)
    elif n.get("pdf"):
        rows = [["확인 실패"] * 6]                                        # 표를 못 읽음 — 채우지 않는다
        fallback = "평형·보증금·월세: 확인 실패 (공고문 확인)"
    else:
        rows = None                                                      # PDF 가 없는 출처(힐스테이트·SH·엘리스 등)
        fallback = "평형·보증금·월세: 공고문 확인"

    if rows and kept and not complete:
        rows.append(["확인 실패"] * 6)                                   # 읽지 못한 표가 더 있음
    kind = kind_of(title) if title else "모집공고"
    note = f"단위: 만원 · 보증금 비율마다 한 행 · {kind} · 자격 공고문 확인"
    if not complete and types:
        note += " · 일부 표를 읽지 못함(공고문 확인)"
    if len(kept) < len(types) and not skip:
        note += f" · 전용 {MIN_AREA:g}㎡ 미만 {len(types) - len(kept)}개 타입 제외"
    return {"top": top, "flags": ["📐 면적 확인 필요"] if area_unknown else [],
            "rows": rows, "caption": f"{name} 임대조건", "note": note, "fallback_text": fallback,
            "urgent": urgent, "skip": skip, "dropped_types": len(types) - len(kept),
            "notice_url": n["url"], "apply_url": _apply_url(n),
            # 예전 카드 형식(send_notices)과의 호환용
            "title": top, "summary": fallback, "meta": note}


def _with_deadline(n: dict, d: dt.date) -> dict:
    """견본용: 마감일만 바꾼 사본. 미리 계산된 D-day 값은 지워서 새 마감일로 다시 센다."""
    m = {k: v for k, v in n.items() if k not in ("dday", "days_left")}
    m["deadline"] = f"{d.month}/{d.day}"
    return m


def slack_test() -> int:
    """수동 실행 '슬랙 시험': 최근 실제 공고로 견본 3건을 보낸다(공고 1건 = 메시지 1개, 표 형식).
    1) 일반  2) 마감 임박(D-1)  3) 면적 확인 필요(PDF 표에 전용면적이 없는 공고).
    마감일은 견본용 가상값. 상태 파일·카톡은 건드리지 않는다."""
    slk = Slack.from_env()
    if not slk:
        print("slack test: SLACK_WEBHOOK_URL 이 없습니다")
        return 1
    today = dt.datetime.now(KST).date()
    picks: dict[str, dict] = {}
    try:
        for n in S.fetch_soco(0, max_pages=3):
            if len(picks) == 3:
                break
            if not n.get("pdf"):
                continue
            n["types"], n["types_complete"] = fetch_types(n["pdf"], with_status=True)
            if not n["types"]:
                continue
            c = slack_item(n, today, fetch_pdf=False)
            if c["skip"]:
                continue
            if c["flags"]:
                picks.setdefault("area", n)
            elif "normal" not in picks:
                picks["normal"] = n
            elif "soon" not in picks:
                picks["soon"] = n
    except Exception as e:
        print(f"slack test: 견본 공고 읽기 실패 ({type(e).__name__})")
    if len(picks) < 3:
        print(f"slack test: 견본으로 쓸 공고를 다 찾지 못함 ({sorted(picks)})")
        slk.send_text("🧪 seoul-notice-bot 슬랙 시험: 견본 공고를 다 찾지 못했습니다(연결만 확인됨)")
        return 1

    plan = [("normal", "일반", today + dt.timedelta(days=10)),
            ("soon", "마감 임박 D-1", today + dt.timedelta(days=1)),
            ("area", "면적 확인 필요", today + dt.timedelta(days=10))]
    samples = [(label, slack_item(_with_deadline(picks[k], d), today, fetch_pdf=False), picks[k].get("board_id"))
               for k, label, d in plan]
    # 4) 마이홈 API 공고(실제 값 그대로, 면적 없음 → 📐) — 키가 있을 때만
    if os.environ.get("MYHOME_API_KEY", "").strip():
        try:
            mh = [x for x in MH.fetch_notices(today) if S.mine(x["title"])]
            if mh:
                x = mh[0]
                n = {"title": x["title"], "name": x["title"][:40], "deadline": x["deadline"],
                     "days_left": x["days_left"], "dday": f"D-{x['days_left']}" if x["days_left"] else "D-DAY",
                     "types": x["types"], "types_complete": True, "url": x["url"], "apply_url": x["url"], "pdf": None}
                samples.append(("마이홈 API", slack_item(n, today, fetch_pdf=False), f"myhome:{x['id']}"))
            else:
                print("slack test: 마이홈 견본으로 쓸 범위 안 모집공고 없음")
        except Exception as e:
            print(f"slack test: 마이홈 견본 읽기 실패 ({MH.redact(str(e))[:60]})")
    all_ok = True
    total = len(samples)
    for i, (label, c, bid) in enumerate(samples, 1):
        c["top"] = f"[견본 {i}/{total}] " + c["top"]
        c["note"] += (" · 🧪 시험(실제 값, 상태 파일 변경 없음)" if label == "마이홈 API"
                      else " · 🧪 시험(마감일은 견본용 가상값, 상태 파일 변경 없음)")
        ok = slk.send_notice(c)
        all_ok = all_ok and ok
        print(f"slack test: 견본 {i}/{total} {label} — {'ok' if ok else 'fail'} "
              f"(board_id {bid}, 표 {len(c['rows'] or [])}행, 형식 {slk.table_mode or 'text'})")
    print(f"slack test: {'ok' if all_ok else 'fail'} · 표 형식 {slk.table_mode or '없음'}")
    return 0 if all_ok else 1


def elyes_preview(days: int = 4) -> int:
    """엘리스 최근 글을 '처음 보는 글'로 치고 카드만 로그에 찍는다. 슬랙·상태 파일은 건드리지 않는다.
    (워크플로 수동 실행 elyes_preview=1 — 실행 환경에서 글자 인식이 되는지 확인용)"""
    now = dt.datetime.now(KST).replace(tzinfo=None)
    since = (now.date() - dt.timedelta(days=days)).strftime("%Y.%m.%d")
    rows = [x for x in S.fetch_elyes(S.elyes_regions({})) if x["date"] >= since]
    print(f"엘리스 미리보기 · 기준 {now:%Y-%m-%d %H:%M} KST · {since} 이후 글 {len(rows)}건 · OCR {'가능' if OCR.available() else '불가'}")
    for x in rows:
        if not x["mine"]:
            print(f"\n[제외: 조건 밖·모집 아님] {x['title']}")
            continue
        n = {"board_id": f"elyes:{x['id']}", "source": "elyes", "title": f"롯데 엘리스 — {x['title']}",
             "name": x["title"][:40], "raw_title": x["title"], "url": x["url"], "pdf": None,
             "images": x["images"], "posted": x["date"]}
        e = elyes_enrich(n, now)
        if e is None:
            print(f"\n[보내지 않음: 마감 지남] {x['title']}")
            continue
        c = slack_item(e, now.date())
        if c["skip"]:
            print(f"\n[보내지 않음: 모든 타입 전용 {MIN_AREA:g}㎡ 미만] {x['title']}")
            continue
        print(f"\n[보냄] {x['title']}\n  " + "\n  ".join(c["flags"] + [c["top"]]))
        if c["rows"]:
            for r in [c["head"]] + c["rows"]:
                print("  | " + " | ".join(r) + " |")
        else:
            print("  " + c["fallback_text"])
        print(f"  {c['note']}\n  [공고 보기] {c['notice_url']}")
    return 0


def main() -> int:
    if os.environ.get("ELYES_PREVIEW") == "1":
        return elyes_preview()
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
        for r in ([] if QUIET else urgent_open):
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
        small = [c for c in cards if c["skip"]]
        cards = [c for c in cards if not c["skip"]]
        print(f"slack: 전용 {MIN_AREA:g}㎡ 미만으로 제외 {len(small)}건"
              + (" — " + ", ".join(c["top"][:30] for c in small) if small else ""))
        n_urgent = sum(1 for c in cards if c["urgent"])
        n_new = sum(1 for c in cards if not c["urgent"])
        if len(cards) > 1:
            header = f"🏠 신규 {n_new}건 · 마감임박 {n_urgent}건"
        elif not cards and errors:
            header = f"⚠ {stamp} 확인 일부 실패 · 확인된 곳에는 신규·마감임박 없음"
        elif not cards:
            header = f"✅ {stamp} 확인 완료 · 신규·마감임박 없음"
        else:
            header = ""
        footer = f"⚠ 확인 실패 {len(errors)}건: " + ", ".join(e[:40] for e in errors) if errors else ""
        if QUIET and not cards:
            header = footer = ""                   # 추가 실행은 새 공고가 있을 때만 보낸다
            print("slack: 추가 실행 — 새 공고 없음, 보내지 않음")
        if header or footer:                       # 요약 줄은 따로 한 메시지, 공고는 1건 = 메시지 1개
            if slk.send_text("\n".join(x for x in (f"*{header}*" if header else "", footer) if x)):
                slack_msgs += 1
            else:
                slack_failed += 1
        for c in cards:
            if slk.send_notice(c):
                slack_msgs += 1
            else:
                slack_failed += 1
        print(f"slack: 공고 {len(cards)}건 / 메시지 {slack_msgs}건 성공 / {slack_failed}건 실패"
              f" / 표 형식 {slk.table_mode or '없음'}")
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
    if "_hillstate_seen_new" in state:
        state["hillstate_seen"] = state.pop("_hillstate_seen_new")
    for k in [k for k in state if k.startswith("_new_")]:
        state[k[len("_new_"):]] = state.pop(k)
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
