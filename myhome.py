"""마이홈포털 공공주택 모집공고 API(data.go.kr 15108420) — 공공임대 모집공고 출처.

- 인증키는 env MYHOME_API_KEY 에서만 읽는다. 키·요청 주소·예외 원문은 출력하지 않는다.
- 이 API 에는 전용면적이 없다 → 카드에는 항상 '📐 면적 확인 필요'.
- 공공지원민간임대(청년안심주택 등)는 이 API 에 들어오지 않는다(2026-11-19 GitHub 점검: 전국 293건 중 0건).
"""
from __future__ import annotations
import datetime as dt, os, re
from urllib.parse import quote, unquote
import requests

BASE = "https://apis.data.go.kr/1613000/HWSPR02/rsdtRcritNtcList"
SEOUL, GYEONGGI = "11", "41"
# 서울에서 가까운 남쪽 경기도 — 사용자가 정한 범위(2026-11-19)
NEAR_CITIES = ("과천시", "안양시", "성남시", "광명시", "의왕시", "군포시", "하남시")


def _key() -> str:
    return os.environ.get("MYHOME_API_KEY", "").strip()


def redact(t: str) -> str:
    k = _key()
    for v in sorted({k, quote(k, safe=""), unquote(k)} - {""}, key=len, reverse=True):
        t = t.replace(v, "***")
    return re.sub(r"(serviceKey=)[^&\s\"'<]+", r"\1***", t, flags=re.I)


def _get(params: dict) -> dict:
    k = _key()
    if not k:
        raise RuntimeError("MYHOME_API_KEY 없음")
    sk = k if "%" in k else quote(k, safe="")          # 키는 받은 그대로(인코딩 키는 다시 인코딩하지 않음)
    qs = "&".join(f"{a}={quote(str(b), safe='')}" for a, b in params.items())
    try:
        r = requests.get(f"{BASE}?serviceKey={sk}&{qs}", timeout=40)
    except Exception as e:
        raise RuntimeError(f"연결 오류 {type(e).__name__}") from None
    try:
        j = r.json()
    except ValueError:
        m = re.search(r"[A-Z_]{8,}_ERROR", r.text)
        raise RuntimeError(f"HTTP {r.status_code} {m.group(0) if m else 'JSON 아님'}") from None
    hdr = (j.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader") or {}
    if hdr.get("errMsg"):
        raise RuntimeError(redact(f"HTTP {r.status_code} {hdr['errMsg']}"))
    head = (j.get("response") or {}).get("header") or {}
    if r.status_code != 200 or head.get("resultCode") not in (None, "00"):
        raise RuntimeError(redact(f"HTTP {r.status_code} {head.get('resultCode')} {head.get('resultMsg')}"))
    return (j.get("response") or {}).get("body") or {}


def _items(body: dict) -> list[dict]:
    it = body.get("item")
    if it is None:
        it = (body.get("items") or {}).get("item") if isinstance(body.get("items"), dict) else body.get("items")
    if isinstance(it, dict):
        return [it]
    return list(it or [])


def fetch_items(brtc: str, max_pages: int = 10) -> list[dict]:
    out, total, pg = [], None, 1
    while pg <= max_pages:
        body = _get({"brtcCode": brtc, "numOfRows": "100", "pageNo": str(pg)})
        more = _items(body)
        try:
            total = int(str(body.get("totalCount") or "").replace(",", ""))
        except ValueError:
            total = None                              # 모르면 쪽이 꽉 찬 동안 계속 넘긴다
        out += more
        if not more or (total is not None and len(out) >= total) or (total is None and len(more) < 100):
            break
        pg += 1
    return out


def in_area(x: dict) -> bool | None:
    """True 범위 안 · False 범위 밖 · None 알 수 없음(경기도인데 시군구·주소가 비어 있음)."""
    brtc = str(x.get("brtcNm") or "")
    sg = str(x.get("signguNm") or "")
    addr = str(x.get("fullAdres") or "")
    if brtc.startswith("서울"):
        return True
    if not brtc.startswith("경기"):
        return False
    if sg:
        return sg.startswith(NEAR_CITIES)
    if addr:
        return any(c in addr or c[:-1] + " " in addr for c in NEAR_CITIES)
    return None


def money(v, kind: str) -> float | None:
    """원 → 만원. API 명세에 단위가 없어 크기로 짐작하지 않는다:
    원 단위가 분명한 값(보증금 ≥ 100만, 월세 ≥ 1만)만 바꾸고, 0·빈 값·그보다 작은 값은 None('확인 실패')."""
    try:
        n = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if n >= (1_000_000 if kind == "dep" else 10_000):
        return round(n / 10_000, 2)
    return None


def _date(s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(str(s)[:8], "%Y%m%d").date()
    except ValueError:
        return None


def notices(items: list[dict], today: dt.date, log=print) -> list[dict]:
    """API 항목(단지별) → 공고 단위로 묶는다. 범위 밖 단지는 빼고, 남은 단지가 없으면 공고도 뺀다."""
    groups: dict[str, list[dict]] = {}
    for x in items:
        groups.setdefault(str(x.get("pblancId") or x.get("pblancNm")), []).append(x)
    out, outside, unknown = [], 0, 0
    for pid, xs in groups.items():
        flags = [in_area(x) for x in xs]
        keep = [x for x, f in zip(xs, flags) if f]
        if not keep:
            if any(f is None for f in flags):
                unknown += 1
                log(f"myhome: 지역 확인 불가로 제외 — {str(xs[0].get('pblancNm'))[:40]}")
            else:
                outside += 1
            continue
        x0 = keep[0]
        end = _date(x0.get("endDe") or "")
        left = (end - today).days if end else None
        if left is not None and left < 0:
            continue                                  # 이미 접수 마감
        types = []
        for x in keep:
            log(f"myhome: 값 | {str(x.get('pblancNm'))[:30]} | {x.get('signguNm') or x.get('brtcNm')} | "
                f"보증금 원값 {x.get('rentGtn')!r}→{money(x.get('rentGtn'), 'dep')} · 월세 원값 {x.get('mtRntchrg')!r}→{money(x.get('mtRntchrg'), 'rent')}")
            label = str(x.get("hsmpNm") or x.get("houseTyNm") or "확인 실패")[:20]
            t = {"kind": str(x.get("suplyTyNm") or ""), "label": label, "area": "",
                 "prices": [{"ratio": "기준", "deposit": money(x.get("rentGtn"), "dep"),
                             "rent": money(x.get("mtRntchrg"), "rent")}]}
            if t not in types:
                types.append(t)
        out.append({
            "id": pid, "title": str(x0.get("pblancNm") or ""), "insttNm": str(x0.get("suplyInsttNm") or ""),
            "suplyTyNm": str(x0.get("suplyTyNm") or ""), "region": f"{x0.get('brtcNm') or ''} {x0.get('signguNm') or ''}".strip(),
            "deadline": end.isoformat() if end else "", "days_left": left,
            "url": str(x0.get("pcUrl") or x0.get("url") or "https://www.myhome.go.kr"), "types": types})
    log(f"myhome: 공고 {len(out)}건(범위 안) · 범위 밖 제외 {outside}건 · 지역 확인 불가 {unknown}건")
    return out


def fetch_notices(today: dt.date, log=print) -> list[dict]:
    items = fetch_items(SEOUL) + fetch_items(GYEONGGI)
    log(f"myhome: 서울+경기 항목 {len(items)}개 받음")
    return notices(items, today, log)
