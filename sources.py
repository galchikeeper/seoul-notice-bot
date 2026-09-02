"""공고 수집 — 서울시 청년안심주택 + 힐스테이트 관악 뉴포레 + 공간지원리츠."""
from __future__ import annotations
import re, io, datetime as dt
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; seoul-notice-bot/1.0)"}
TIMEOUT = 30

SOCO_JSON = ("https://soco.seoul.go.kr/youth/pgm/home/yohome/bbsListJson.json"
             "?bbsId=BMSR00015&menuNo=400008&pageIndex={page}")
SOCO_VIEW = "https://soco.seoul.go.kr/youth/bbs/BMSR00015/view.do?boardId={bid}&menuNo=400008"
SOCO_FILE = "https://soco.seoul.go.kr/coHouse/cmmn/file/fileDown.do?atchFileId={fid}&fileSn=1"

HILLSTATE = "https://hillstatenewfore.co.kr/sub/sub05_01.php"
SPACEREITS = {
    "seongdong":   "성동스페이스",
    "yeongdeungpo": "양평동 동문 디 이스트",
    "gangdong":    "강동밀레니얼 중흥S클래스",
    "mokdong":     "목동스페이스",
    "seongnae":    "성내스페이스",
}

# 공실 회전이 빨라 우선 알림 대상인 단지
FAST_TURNOVER = [
    "비바힐스강변", "세이지움 상봉", "상봉동양엔파트", "라온프라이빗 종암",
    "라봄성동", "더써밋타워", "BX201", "세이지움 개봉",
]


def _strip(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)


# ─────────────────────────── 청년안심주택 ───────────────────────────

def fetch_soco(last_board_id: int, max_pages: int = 3) -> list[dict]:
    """boardId 가 last_board_id 보다 큰 신규 공고를 최신순으로 반환.

    주의: 응답의 rnum 은 페이지 안 순번(1~10)일 뿐이고, 게시판 화면에 보이는
    번호는 totRow 에서 역산한 값이다. 증가하는 고유 키는 boardId 뿐이므로
    신규 판정은 반드시 boardId 로 한다.
    """
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        r = requests.get(SOCO_JSON.format(page=page), headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("resultList", [])
        tot = int(payload.get("pagingInfo", {}).get("totRow") or 0)
        if not rows:
            break
        stop = False
        for it in rows:
            bid = int(it.get("boardId") or 0)
            if bid <= last_board_id:
                stop = True
                continue
            rnum = int(it.get("rnum") or 0)
            display_no = tot - ((page - 1) * 10 + rnum - 1) if tot else 0
            body = _strip(it.get("content"))
            out.append({
                "source": "soco",
                "board_no": display_no,   # 게시판 화면에 보이는 번호(참고용)
                "board_id": int(it.get("boardId") or 0),
                "title": (it.get("nttSj") or "").strip(),
                "posted": it.get("optn1"),
                "apply_from": it.get("optn4"),
                "operator": (it.get("optn3") or "").strip(),
                "file_id": it.get("atchFileId"),
                "body": body,
                "url": SOCO_VIEW.format(bid=it.get("boardId")),
                "pdf": SOCO_FILE.format(fid=it["atchFileId"]) if it.get("atchFileId") else None,
                **parse_body(body),
            })
        if stop:
            break
    return sorted(out, key=lambda x: x["board_id"], reverse=True)


def parse_body(body: str) -> dict:
    """공고 본문(단지개요)에서 위치·공급호수·청약기간·신청사이트를 뽑는다."""
    def one(pat):
        m = re.search(pat, body)
        return m.group(1).strip() if m else ""

    addr = one(r"주택위치\s*[:：]\s*(.+)")
    station = ""
    m = re.search(r"[（(]([^)）]*(?:호선|출구)[^)）]*)[)）]", addr)
    if m:
        station = m.group(1).strip()
        addr = addr[:m.start()].strip()

    apply_raw = one(r"청약신청\s*[:：]\s*(.+)")
    site = one(r"(https?://[^\s,)]+)")
    if "soco.seoul.go.kr" in site:
        site = ""

    return {
        "addr": addr,
        "station": station,
        "supply": one(r"공급호수\s*[:：]\s*(.+)"),
        "apply_raw": apply_raw,
        "deadline": _deadline(apply_raw),
        "site": site,
        "tel": one(r"문의전화\s*[:：]?\s*([\d\-]{9,})"),
    }


def _deadline(raw: str) -> str:
    """'‘26. 09. 11. (금) 09:00 ~ 09. 14. (월) 23:00' → '9/14(월) 23:00'"""
    if not raw:
        return ""
    tail = raw.split("~")[-1]
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*[（(]?([월화수목금토일])?[)）]?\s*(\d{1,2}:\d{2})?", tail)
    if not m:
        return raw.strip()[:24]
    mm, dd, wd, hm = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
    s = f"{int(mm)}/{int(dd)}"
    if wd:
        s += f"({wd})"
    if hm:
        s += f" {hm}"
    return s


def dday(deadline: str, today: dt.date) -> str:
    m = re.match(r"(\d{1,2})/(\d{1,2})", deadline or "")
    if not m:
        return ""
    mm, dd = int(m.group(1)), int(m.group(2))
    year = today.year + (1 if mm < today.month - 6 else 0)
    try:
        d = dt.date(year, mm, dd)
    except ValueError:
        return ""
    n = (d - today).days
    return "D-DAY" if n == 0 else (f"D-{n}" if n > 0 else "마감")


# ─────────────────────────── 공고문 PDF ───────────────────────────

AREA_RE = r"(\d{2,3}\.\d{1,2})\s*(?:㎡|m2|m²)"
MONEY_RE = r"(\d{1,3}(?:,\d{3})+|\d{4,})"


def fetch_pdf_types(pdf_url: str, limit: int = 4) -> list[dict]:
    """공고문 PDF에서 (전용면적, 보증금, 월세) 후보를 최선 노력으로 추출.

    공고문 서식이 사업자마다 달라 실패할 수 있다. 실패 시 빈 리스트를 돌려주고
    카드에는 'PDF 확인'으로 표시한다.
    """
    try:
        import pdfplumber
        r = requests.get(pdf_url, headers=UA, timeout=60)
        r.raise_for_status()
        rows: list[dict] = []
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            for page in pdf.pages[:6]:
                for tbl in (page.extract_tables() or []):
                    rows += _rows_from_table(tbl)
                if len(rows) >= limit:
                    break
        # 면적 기준 오름차순, 중복 제거
        seen, uniq = set(), []
        for row in sorted(rows, key=lambda x: x["area_val"]):
            key = round(row["area_val"], 1)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(row)
        return uniq[:limit]
    except Exception:
        return []


def _rows_from_table(tbl) -> list[dict]:
    out = []
    for raw in tbl:
        cells = [(c or "").replace("\n", " ").strip() for c in raw]
        line = " | ".join(cells)
        a = re.search(AREA_RE, line)
        if not a:
            continue
        money = re.findall(MONEY_RE, line.replace(a.group(0), ""))
        money = [m for m in money if int(m.replace(",", "")) >= 10]
        if len(money) < 2:
            continue
        big = [m for m in money if int(m.replace(",", "")) >= 1000]
        small = [m for m in money if int(m.replace(",", "")) < 1000]
        if not big or not small:
            continue
        label = next((c for c in cells if re.search(r"청년|신혼|일반|특별|[A-Z]?\d{2}[A-Z]?", c)
                      and not re.search(r"\d{3,}", c)), "")
        out.append({
            "label": (label or "공급")[:12],
            "area": f"전용 {a.group(1)}㎡",
            "area_val": float(a.group(1)),
            "deposit": f"{big[0]}~{big[-1]}" if len(big) > 1 else big[0],
            "rent": f"{small[0]}~{small[-1]}" if len(small) > 1 else small[0],
        })
    return out


# ─────────────────────── 힐스테이트 / 공간지원리츠 ───────────────────────

def fetch_hillstate() -> dict:
    r = requests.get(HILLSTATE, headers=UA, timeout=TIMEOUT)
    r.encoding = r.apparent_encoding
    text = _strip(r.text)
    m = re.search(r"전체\s*[:：]\s*(\d+)", text)
    count = int(m.group(1)) if m else 0
    soup = BeautifulSoup(r.text, "html.parser")
    titles = [a.get_text(strip=True) for a in soup.select("a") if a.get_text(strip=True)]
    latest = next((t for t in titles if "모집" in t or "발표" in t or "공지" in t), "")
    return {"count": count, "latest": latest, "url": HILLSTATE}


def fetch_spacereits(slug: str) -> dict:
    url = f"https://spacereits.co.kr/{slug}/notice"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, "html.parser")
        rows = [tr.get_text(" ", strip=True) for tr in soup.select("tr")]
        rows = [x for x in rows if re.search(r"\d{2}\.\d{2}\.\d{2}", x)]
        return {"latest": rows[0][:120] if rows else "", "count": len(rows), "url": url}
    except Exception:
        return {"latest": "", "count": -1, "url": url, "error": True}
