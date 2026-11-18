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
        # 신청 버튼용: 본문의 '청약신청 페이지 : https://…' 줄에 적힌 주소만 쓴다
        "apply_url": one(r"청약신청\s*페이지\s*[:：]\s*(https?://[^\s,)）<]+)"),
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
    """공지 게시판의 글 줄(번호·제목·등록일)을 돌려준다.

    예전에는 페이지의 모든 링크 중 '모집/발표/공지'가 든 첫 글자를 최신 글로 봤는데,
    메뉴 '공공지원민간임대주택'('공공지원' 안의 '공지')이 먼저 잡혀 모집 판정이 늘 '아님'이었다.
    """
    r = _get_retry(HILLSTATE)
    r.encoding = r.apparent_encoding
    text = _strip(r.text)
    m = re.search(r"전체\s*[:：]\s*(\d+)", text)
    count = int(m.group(1)) if m else 0
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for tr in soup.select("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        if len(tds) < 4:
            continue
        date = next((x for x in tds if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x)), "")
        if not date:
            continue
        title = tds[1]
        a = tr.select_one("a[href]")
        rows.append({"no": tds[0], "title": title, "date": date,
                     "key": f"{date}|{title}"[:150], "is_recruit": is_recruit(title),
                     "href": a["href"] if a else ""})
    return {"count": count, "rows": rows, "url": HILLSTATE}


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


# ─────────────────────────── SH 주택임대 공고 ───────────────────────────

SH_LIST = "https://www.i-sh.co.kr/main/lay2/program/S1T294C297/www/brd/m_247/list.do"
SH_VIEW = "https://www.i-sh.co.kr/main/lay2/program/S1T294C297/www/brd/m_247/view.do?seq={seq}"
# 조건(만 39세 이하·신혼·무주택, 주거용)에 안 맞는 글
NOT_MINE_RE = re.compile(r"(장애인\s*전용|상가|점포|업무시설|오피스(?!텔)|사무실|공장|주차|"
                         r"채용|신입사원|용역|입찰|공사|업무\s*시간|현황|마감되었습니다|계약\s*결과|예비\s*당첨|당첨자)")
ELDER_RE = re.compile(r"(고령자|어르신|노인)")
YOUNG_RE = re.compile(r"(청년|신혼|신생아|대학생|사회초년)")


def mine(title: str) -> bool:
    """모집공고이면서 사용자 조건에 안 맞는 글이 아닌 것.
    고령자 전용은 빼되, '청년·고령자'처럼 청년·신혼이 함께 적힌 공고는 남긴다."""
    if not is_recruit(title) or NOT_MINE_RE.search(title):
        return False
    return not (ELDER_RE.search(title) and not YOUNG_RE.search(title))


def fetch_sh(pages: int = 3) -> list[dict]:
    """첫 3쪽(약 30건). 주말·장애로 며칠 밀려도 놓치지 않게 여러 쪽을 본다."""
    out = []
    for pg in range(1, pages + 1):
        out += _sh_page(pg)
    return list({x["seq"]: x for x in out}.values())


def _sh_page(pg: int) -> list[dict]:
    r = _get_retry(f"{SH_LIST}?page={pg}")
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for tr in soup.select("tbody tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.select("td")]
        a = tr.select_one("a[onclick]")
        m = re.search(r"getDetailView\('(\d+)'\)", a.get("onclick", "")) if a else None
        if len(tds) < 4 or not m or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tds[3]):
            continue
        title = re.sub(r"^NEW\s+", "", tds[1])
        out.append({"seq": m.group(1), "title": title, "dept": tds[2], "date": tds[3],
                    "url": SH_VIEW.format(seq=m.group(1)), "mine": mine(title)})
    return out


# ─────────────────────────── 롯데 엘리스 모집공고 ───────────────────────────

ELYES = "https://www.elyes.co.kr"
ELYES_FORM_H = {**UA, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
NON_SEOUL_RE = re.compile(r"(경기도|부산광역시|인천광역시|대구광역시|광주광역시|대전광역시|울산광역시|"
                          r"세종특별|충청|전라|경상|강원|제주)[가-힣]*\s*[가-힣]+(?:시|군|구)")


def _norm(name: str) -> str:
    return re.sub(r"\s+|역|\(청년안심주택\)", "", name or "")


NEAR_RE = re.compile(r"경기도\s*(과천|안양|성남|광명|의왕|군포|하남)시")   # 사용자가 정한 서울 근교 7곳


def elyes_regions(cache: dict) -> dict:
    """단지명 → '서울' / '근교'(경기 7곳) / '범위 밖'. 단지 페이지 본문의 주소로 판단한다.
    (본문 아래 회사 주소 '서울 서초구 잠원로'는 모든 페이지에 있어 서울 판별에 쓰지 않는다)"""
    r = requests.post(f"{ELYES}/complex/data/stay/list-all", headers=ELYES_FORM_H, timeout=TIMEOUT)
    out = dict(cache)
    for x in r.json().get("stayList", []):
        name = x.get("proj_name") or ""
        if not name or name in out or not x.get("landing_url"):
            continue
        from urllib.parse import urljoin
        try:
            t = requests.get(urljoin(ELYES + "/", x["landing_url"]), headers=UA, timeout=TIMEOUT).text
        except Exception:
            continue                       # 이번엔 모름 → 저장하지 않고 다음 실행에서 다시 본다
        if re.search(r"서울(?:특별시|시)?\s*[가-힣]+구", t.replace("서초구 잠원로", "")) or NON_SEOUL_RE.search(t):
            if not NON_SEOUL_RE.search(t):
                out[name] = "서울"
            else:
                out[name] = "근교" if NEAR_RE.search(t) else "범위 밖"
    return out


def elyes_region_of(title: str, regions: dict) -> str:
    m = re.match(r"\s*\[([^\]]+)\]", title)
    if not m:
        return "미확인"
    b = _norm(m.group(1))
    for name, reg in regions.items():
        n = _norm(name)
        if b and (b in n or n in b):
            return reg
    return "미확인"


def fetch_elyes(regions: dict) -> list[dict]:
    import html as _html
    r = requests.post(f"{ELYES}/post/data/recruit/list", headers=ELYES_FORM_H, timeout=TIMEOUT,
                      data="searchSelect=title&searchKeyword=&i_iNowPageNo=1&i_iPageSize=60")   # 며칠 밀려도 놓치지 않게 넉넉히
    r.raise_for_status()
    out = []
    for x in r.json().get("recruitList", []):
        title = _html.unescape(x.get("nt_title") or "").strip()
        if not title:
            continue
        reg = elyes_region_of(title, regions)
        out.append({"id": x.get("nt_idx"), "title": title, "date": x.get("row_input_date") or "",
                    "region": reg, "url": f"{ELYES}/post/recruit",
                    "mine": mine(title) and reg not in ("범위 밖", "서울 밖")})
    return out
