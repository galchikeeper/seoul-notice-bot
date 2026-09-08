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

HILLSTATE = "https://www.hillstatenewfore.co.kr/sub/sub05_01.php"
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


def _get_retry(url: str, tries: int = 3, wait: float = 2.0):
    """spacereits/hillstate 는 연결이 산발적으로 끊긴다. 몇 번 다시 시도한다."""
    import time
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(wait * (i + 1))
    raise last


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


DATE_RE = re.compile(
    r"(\d{1,2})\.\s*(\d{1,2})\.\s*[（(]?([월화수목금토일])?[)）]?\s*(\d{1,2}:\d{2})?")


def _deadline(raw: str) -> str:
    """'‘26. 09. 11. (금) 09:00 ~ 09. 14. (월) 23:00' → '9/14(월) 23:00'

    당일 접수('26. 09. 07. (월) 00:00 ~ 23:00)처럼 ~ 뒤에 날짜가 없는 경우가 있어,
    ~ 뒤에서 날짜를 못 찾으면 문자열 전체의 마지막 날짜를 마감일로 본다.
    """
    if not raw:
        return ""
    # 연도 표기('‘26.', "'26.", '2026.', '2026년')를 먼저 걷어낸다.
    # 안 걷어내면 '‘26. 09. 07.' 에서 26 을 '월'로 잡아 파싱이 깨진다.
    raw = re.sub(r"[‘’'\"`]\s*\d{2}\s*\.", " ", raw)
    raw = re.sub(r"\b20\d{2}\s*[.년]", " ", raw)

    def valid(mo):                             # '‘26. 09.' 의 26 을 월로 잡지 않도록
        return 1 <= int(mo.group(1)) <= 12 and 1 <= int(mo.group(2)) <= 31

    tail = raw.split("~")[-1]
    m = next((x for x in DATE_RE.finditer(tail) if valid(x)), None)
    if not m:                                  # ~ 뒤에 날짜 없음 → 전체의 마지막 날짜
        all_m = [x for x in DATE_RE.finditer(raw) if valid(x)]
        if not all_m:
            return raw.strip()[:24]
        m = all_m[-1]
        hm = re.search(r"(\d{1,2}:\d{2})\s*$", raw.strip())
        mm, dd, wd = m.group(1), m.group(2), m.group(3) or ""
        s = f"{int(mm)}/{int(dd)}"
        if wd:
            s += f"({wd})"
        if hm:
            s += f" {hm.group(1)}"
        return s
    mm, dd, wd, hm = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
    s = f"{int(mm)}/{int(dd)}"
    if wd:
        s += f"({wd})"
    if hm:
        s += f" {hm}"
    return s


def dday_days(deadline: str, today: dt.date) -> int | None:
    """마감까지 남은 일수. 파싱 못 하면 None."""
    m = re.match(r"(\d{1,2})/(\d{1,2})", deadline or "")
    if not m:
        return None
    mm, dd = int(m.group(1)), int(m.group(2))
    year = today.year + (1 if mm < today.month - 6 else 0)
    try:
        d = dt.date(year, mm, dd)
    except ValueError:
        return None
    return (d - today).days


def dday(deadline: str, today: dt.date) -> str:
    n = dday_days(deadline, today)
    if n is None:
        return ""
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

RECRUIT_RE = re.compile(r"(모집\s*공고|임차인\s*모집|입주자\s*모집|예비\s*입주자\s*모집)")
# '모집'이 들어가도 모집 자체가 아닌 후속 안내들
NOT_RECRUIT_RE = re.compile(
    r"(추첨\s*결과|당첨자|발표|추가\s*서류|서류\s*제출|입주\s*안내|안내문|"
    r"등기|사용\s*승인|준공|대출|계약\s*안내|공가|이벤트|점검|공사|"
    r"근린생활시설|상가|주차장)")          # 상가 임차인 모집은 주거가 아니다


def is_recruit(title: str) -> bool:
    """제목이 '실제 모집공고'인지. 추첨결과·발표·등기 같은 후속 공지는 제외."""
    t = title or ""
    return bool(RECRUIT_RE.search(t)) and not NOT_RECRUIT_RE.search(t)


def _row_key(text: str) -> tuple[str, str]:
    """게시판 한 줄에서 (제목, 작성일)만 뽑는다.

    조회수는 매일 올라가므로 지문에 넣으면 안 된다. 이걸 넣어둔 탓에
    성동·영등포·강동이 매일 '신규'로 잡히는 오탐이 났었다.
    """
    date = ""
    m = re.search(r"(\d{2}\.\d{2}\.\d{2})", text)
    if m:
        date = m.group(1)
    title = text
    title = re.split(r"\s*(?:관리자|작성일|조회)\s*", title)[0]
    title = re.sub(r"^\s*(공지|notice)\s+", "", title, flags=re.I).strip()
    return title[:100], date


def fetch_hillstate() -> dict:
    r = _get_retry(HILLSTATE)
    r.encoding = r.apparent_encoding
    text = _strip(r.text)
    m = re.search(r"전체\s*[:：]\s*(\d+)", text)
    count = int(m.group(1)) if m else 0
    soup = BeautifulSoup(r.text, "html.parser")
    titles = [a.get_text(strip=True) for a in soup.select("a") if a.get_text(strip=True)]
    latest = next((t for t in titles if "모집" in t or "발표" in t or "공지" in t), "")
    return {"count": count, "latest": latest,
            "is_recruit": is_recruit(latest), "url": HILLSTATE}


def fetch_spacereits(slug: str) -> dict:
    """공지 목록의 최신 글 제목·날짜를 돌려준다. www 없는 주소는 리다이렉트가 불안정하다."""
    url = f"https://www.spacereits.co.kr/{slug}/notice"
    try:
        r = _get_retry(url)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, "html.parser")
        rows = [tr.get_text(" ", strip=True) for tr in soup.select("tr")]
        rows = [x for x in rows if re.search(r"\d{2}\.\d{2}\.\d{2}", x)]
        if not rows:
            return {"title": "", "date": "", "key": "", "is_recruit": False,
                    "count": 0, "url": url}
        title, date = _row_key(rows[0])
        return {"title": title, "date": date, "key": f"{date}|{title}",
                "is_recruit": is_recruit(title), "count": len(rows), "url": url}
    except Exception:
        return {"title": "", "date": "", "key": "", "is_recruit": False,
                "count": -1, "url": url, "error": True}
