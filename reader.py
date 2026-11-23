"""공고 읽기 공통 순서 — 모든 출처가 같은 길로 간다(2026-11-23).

  1) 공고 페이지 열기(브라우저처럼 — SH 는 UA 가 없거나 curl 이면 307 오류 페이지로 보냄)
  2) 첨부(PDF·HWP·HWPX·이미지·DOCX) 찾기 → 내려받기(간격 두고 3회)
  3) 글자 추출: PDF 글자 → 글자가 거의 없으면 쪽 그림 OCR / HWP 본문 / HWPX XML / 이미지 OCR
  4) 임대조건(표 → 안 되면 글자 줄)·자격·일정(마감)을 채운다 — fill()

출처별로 다른 건 '첨부를 어디서 찾나'(attachments) 하나뿐이다. 나머지는 전부 같다.
"""
from __future__ import annotations
import io, json, re, time, zipfile, zlib, struct
from urllib.parse import urljoin

import requests

BROWSER_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
              "Accept-Language": "ko-KR,ko;q=0.9"}
WAITS = [3, 10, 20]            # 재시도 간격(초). 시험에서는 READ_WAITS=0 으로 줄인다
EXTS = ("pdf", "hwp", "hwpx", "jpg", "jpeg", "png", "gif", "docx")
# 열린 페이지가 실제 공고가 아니라 오류·로그인 화면인지 판단하는 글자
BAD_PAGE = ("비정상적인 접근", "잘못된 접근", "존재하지 않는 게시물", "삭제된 게시물", "페이지를 찾을 수 없",
            "로그인이 필요", "로그인 후 이용", "error.html", "Access Denied", "403 Forbidden")


def _waits():
    import os
    v = os.environ.get("READ_WAITS")
    return [float(x) for x in v.split(",")] if v else WAITS


DEAD_HOSTS: set[str] = set()     # 이번 실행에서 접속 자체가 안 된 곳(해외 IP 차단 등) — 같은 곳은 다시 기다리지 않는다


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else ""


def get(url: str, tries: int = 3, timeout: int = 60, referer: str = "", session=None) -> requests.Response:
    """간격을 두고 최대 tries 회. 마지막 오류를 그대로 올린다. 접속 오류(시간 초과·연결 끊김)로 끝나면 그 곳을 DEAD_HOSTS 에."""
    if _host(url) in DEAD_HOSTS:
        raise ConnectionError(f"이번 실행에서 {_host(url)} 접속 안 됨")
    s = session or requests
    h = {**BROWSER_UA, **({"Referer": referer} if referer else {})}
    last = None
    for i in range(tries):
        try:
            r = s.get(url, headers=h, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(_waits()[min(i, len(_waits()) - 1)])
    if isinstance(last, (requests.ConnectionError, requests.Timeout)):
        DEAD_HOSTS.add(_host(url))
    raise last


def page_problem(r: requests.Response | None, title: str = "") -> str:
    """열린 페이지 점검: 문제 없으면 ''. 상태 코드·이동한 주소·오류/로그인 글자·제목 글자 포함 여부."""
    if r is None:
        return "열리지 않음"
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    if re.search(r"/error|login", r.url, re.I):
        return f"오류·로그인 화면으로 이동({r.url[:60]})"
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype:
        return ""                                   # 파일 직접 링크(PDF 등)
    text = r.text
    for w in BAD_PAGE:
        if w in text and not _ns(title)[:6] in _ns(text):
            return f"오류 화면('{w}')"
    key = _title_key(title)
    if key and key not in _ns(text):
        return f"제목 글자({key})가 페이지에 없음"
    return ""


def _ns(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s or "")


def _title_key(title: str) -> str:
    """제목에서 페이지에 꼭 있어야 할 글자 한 덩어리(대괄호·출처 접두어 뺀 가장 긴 낱말)."""
    t = re.sub(r"^.*?—\s*", "", title or "")
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)
    words = [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", t)
             if w not in ("입주자", "모집", "공고", "공고문", "모집공고", "입주자모집", "추가모집")]
    return max(words, key=len)[:8] if words else ""


# ─────────────────────────── 첨부 찾기 (출처마다 다른 유일한 곳) ───────────────────────────

def attachments(n: dict, html: str, page_url: str) -> list[dict]:
    """[{name, url}] — 공고문이 앞에 오도록 정렬. 신청서 양식(별지·서식)은 뒤로."""
    out: list[dict] = []
    src = n.get("source") or ("soco" if n.get("pdf") else "")
    if n.get("pdf"):
        out.append({"name": "공고문.pdf", "url": n["pdf"]})
    for u in n.get("images") or []:                             # 엘리스: 공고문이 이미지
        out.append({"name": u.rsplit("/", 1)[-1][:60] or "공고문.jpg", "url": u})
    if html:
        m = re.search(r"initParam\.downList\s*=\s*(\[.*?\]);", html, re.S)   # SH(이노릭스)
        if m:
            base = re.match(r"https?://[^/]+", page_url).group(0)
            try:
                for d in json.loads(m.group(1)):
                    out.append({"name": d.get("oriFileNm", "첨부"),
                                "url": f"{base}/main/com/file/innoFD.do?brdId={d['brdId']}&seq={d['seq']}"
                                       f"&fileTp={d['fileTp']}&fileSeq={d['fileSeq']}"})
            except (ValueError, KeyError):
                pass
        if src != "elyes":
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a[href]"):
                href, label = a["href"], a.get_text(" ", strip=True)
                low = (href + " " + label).lower()
                if _in_menu(a):
                    continue                     # 사이트 메뉴·머리·바닥 링크(예: 힐스테이트 '마감재목록표' PDF)는 공고 첨부가 아님
                m = re.match(r"javascript:\s*download\(\s*'(\w+)'\s*,\s*'(\d+)'\s*,\s*'(\d+)'", href)
                if m:                            # 그누보드식 게시판(힐스테이트): /module/board/download.php
                    out.append({"name": label[:80] or "첨부", "url": urljoin(
                        page_url, f"/module/board/download.php?boardid={m.group(1)}&b_idx={m.group(2)}&idx={m.group(3)}")})
                    continue
                if href.startswith(("#", "javascript")):
                    continue
                if re.search(r"\.(" + "|".join(EXTS) + r")(\?|$|\s)", low) or re.search(
                        r"filedown|download|atchfile|fileDown", href, re.I):
                    out.append({"name": label[:80] or href.rsplit("/", 1)[-1][:60], "url": urljoin(page_url, href)})
    seen, uniq = set(), []
    for x in out:
        if x["url"] not in seen:
            seen.add(x["url"])
            uniq.append(x)
    form = lambda x: bool(re.search(r"별지|서식|양식|신청서|동의서", x["name"]))
    return sorted(uniq, key=lambda x: (form(x), not re.search(r"공고", x["name"])))


MENU_RE = re.compile(r"(^|[-_ ])(gnb|lnb|snb|nav|menu|submenu|header|footer|sitemap|quick)", re.I)


def _in_menu(a) -> bool:
    for p in a.parents:
        if p.name in ("nav", "header", "footer"):
            return True
        if MENU_RE.search(" ".join(p.get("class", []) + [p.get("id") or ""])):
            return True
    return False


def sniff(name: str, data: bytes) -> str:
    """내용 앞부분으로 형식 판별(SH 는 Content-Type 이 octet-stream 이라 이름·헤더를 본다)."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "hwp"                      # OLE 복합 문서 = HWP5(구 doc 은 공고에 안 씀)
    if data[:2] == b"PK":
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return "zip"
        if any(x.startswith("Contents/") for x in names):
            return "hwpx"
        if "word/document.xml" in names:
            return "docx"
        return "zip"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"GIF8":
        return "gif"
    if data[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
        return "html"
    return name.rsplit(".", 1)[-1].lower() if "." in name else "?"


# ─────────────────────────── 글자 추출 ───────────────────────────

def ocr_image(im) -> str:
    import elyes_ocr as OCR
    if not OCR.available():
        return ""
    from PIL import Image
    im = im.convert("L")
    if im.width < 1600:
        im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    return OCR._ocr(im, "kor+eng", 6)


def pdf_text(data: bytes, pages: int = 20) -> tuple[str, str]:
    """pdfplumber 글자 → (열기 실패면) pdfium 글자 → (글자가 거의 없으면) 쪽 그림 OCR."""
    import pdfplumber
    import pypdfium2 as pdfium
    how = "PDF 글자"
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:pages])
    except Exception:
        doc = pdfium.PdfDocument(data)
        text = "\n".join(doc[i].get_textpage().get_text_range() for i in range(min(len(doc), pages)))
        how = "PDF 글자(pdfium — pdfplumber 열기 실패)"
    if len(_ns(text)) >= 300:
        return text, how
    # 글자가 거의 없음 = 스캔·그림 PDF → 쪽을 그림으로 바꿔 OCR
    doc = pdfium.PdfDocument(data)
    out = [ocr_image(doc[i].render(scale=2).to_pil()) for i in range(min(len(doc), 8))]
    return "\n".join(out), "PDF 그림 OCR"


def hwp_text(data: bytes) -> str:
    """HWP5: BodyText/Section* 의 문단 글자(HWPTAG_PARA_TEXT=67)만 꺼낸다. 표 칸 글자도 문단이라 함께 나온다."""
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(data))
    hdr = ole.openstream("FileHeader").read()
    compressed = bool(hdr[36] & 1)
    secs = sorted((s for s in ole.listdir() if s[0] == "BodyText"), key=lambda s: int(re.sub(r"\D", "", s[1]) or 0))
    out = []
    for s in secs:
        raw = ole.openstream(s).read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        i = 0
        while i + 4 <= len(raw):
            h = struct.unpack_from("<I", raw, i)[0]
            tag, size = h & 0x3FF, (h >> 20) & 0xFFF
            i += 4
            if size == 0xFFF:
                size = struct.unpack_from("<I", raw, i)[0]
                i += 4
            if tag == 67:
                t = raw[i:i + size].decode("utf-16le", "ignore")
                out.append(re.sub(r"[\x00-\x1f]", "", t))
            i += size
    return "\n".join(x for x in out if x.strip())


def xml_zip_text(data: bytes, prefix: str) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    parts = []
    for nm in sorted(x for x in z.namelist() if x.startswith(prefix) and x.endswith(".xml")):
        x = z.read(nm).decode("utf-8", "ignore")
        x = re.sub(r"</(hp:p|w:p)>", "\n", x)
        parts.append(re.sub(r"<[^>]+>", "", x))
    return "\n".join(parts)


def extract(name: str, data: bytes) -> tuple[str, str, str]:
    """(형식, 글자, 방법). 실패하면 글자 ''·방법에 이유."""
    kind = sniff(name, data)
    try:
        if kind == "pdf":
            t, how = pdf_text(data)
        elif kind == "hwp":
            t, how = hwp_text(data), "HWP 본문"
        elif kind == "hwpx":
            t, how = xml_zip_text(data, "Contents/section"), "HWPX 본문"
        elif kind == "docx":
            t, how = xml_zip_text(data, "word/document"), "DOCX 본문"
        elif kind in ("jpg", "png", "gif"):
            from PIL import Image
            t, how = ocr_image(Image.open(io.BytesIO(data))), "이미지 OCR"
        else:
            return kind, "", f"읽을 수 없는 형식({kind})"
    except Exception as e:
        return kind, "", f"추출 오류({type(e).__name__})"
    return kind, t, how if _ns(t) else how + " — 글자 0"


# ─────────────────────────── 전체 순서 ───────────────────────────

def read(n: dict, max_files: int = 4, tries: int = 1) -> dict:
    """공고 1건 읽기(재시도는 부르는 쪽 gate.prepare 가 간격 두고 3회). 돌려주는 값:
    {page_url, page_problem, files:[{name,url,kind,size,how,chars,data}], text, problems:[...]}
    출처가 이미 조건을 다 채운 공고(엘리스 OCR 등)는 그림 OCR 을 다시 하지 않는다(파일만 받는다)."""
    need = not (n.get("types") or n.get("ely_rows")) or not n.get("elig") or not n.get("deadline")
    url = n.get("url") or ""
    res = {"page_url": url, "page_problem": "", "files": [], "text": "", "problems": []}
    sess = requests.Session()
    html = ""
    if url:
        try:
            r = get(url, session=sess, tries=tries, timeout=30)
            res["page_problem"] = page_problem(r, n.get("raw_title") or n.get("title", ""))
            html = r.text if "html" in r.headers.get("content-type", "") else ""
        except Exception as e:
            res["page_problem"] = f"열리지 않음({type(e).__name__})"
    texts = []
    for a in attachments(n, html, url)[:max_files]:
        f = {"name": a["name"], "url": a["url"], "kind": "", "size": 0, "how": "", "chars": 0, "data": b""}
        try:
            data = get(a["url"], referer=url, session=sess, timeout=90, tries=tries).content
        except Exception as e:
            f["how"] = f"내려받기 실패({type(e).__name__})"
            res["files"].append(f)
            continue
        f["data"], f["size"] = data, len(data)
        if not need and sniff(a["name"], data) in ("jpg", "png", "gif"):
            f["kind"], t, f["how"] = sniff(a["name"], data), "", "받음(조건은 출처에서 이미 읽음)"
        else:
            f["kind"], t, f["how"] = extract(a["name"], data)
        f["chars"] = len(_ns(t))
        if f["kind"] == "html":
            f["how"] = "파일 대신 웹페이지가 옴(막힘·로그인)"
        res["files"].append(f)
        if t and not re.search(r"별지|서식|양식|동의서", a["name"]):
            texts.append(t)
    if not texts and html:                           # 첨부에서 못 읽으면 다른 방법: 페이지 본문 글자
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for x in soup(["script", "style", "nav", "header", "footer"]):
            x.decompose()
        body = soup.get_text("\n", strip=True)
        texts.append(body)
        res["problems"].append("첨부 글자 없음 → 페이지 본문으로 대체")
    res["text"] = "\n".join(texts)
    return res


# ─────────────────────────── 조건 채우기 ───────────────────────────

YMD = re.compile(r"(20\d{2})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})\s*[.일]?\s*(?:[（(]\s*([월화수목금토일])\s*[)）])?")


def _date_of(m) -> tuple:
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(4) or ""


def deadline_from_text(text: str) -> str:
    """'접수·신청 기간' 머리 줄부터 아래 3줄 중 날짜가 처음 나오는 줄에서 마감을 고른다 → 'M/D(요일)'.
    그 줄에 '~ 날짜'가 있으면 그 날짜(기간의 끝), 없으면 그 줄의 가장 늦은 날짜.
    머리 줄에 발표·추첨 열이 같이 있으면(표가 섞임) '~ 날짜'만 인정. 못 찾으면 ''(관문이 '마감' 실패로 표시)."""
    lines = (text or "").split("\n")
    for i, ln in enumerate(lines):
        if not re.search(r"(접수|신청)(기간|일정|일시|기한|마감)|신청서접수|청약(기간|일정)", _sp(ln)):
            continue
        if re.search(r"등기|계약|입주(기간|예정|시기)|공고일|자격|서류제출|방법", ln):
            continue
        mixed = bool(re.search(r"발표|추첨|당첨", ln))
        for ln2 in lines[i:i + 4]:
            if re.search(r"공고일|계약", ln2):
                continue
            tilde = [_date_of(m) for m in re.finditer(r"~\s*" + YMD.pattern, ln2) if _ok(m)]
            if tilde:
                return _fmt(max(tilde))
            if mixed:
                continue
            ds = [_date_of(m) for m in YMD.finditer(ln2) if _ok(m)]
            if ds:
                return _fmt(max(ds))
    return ""


def _ok(m) -> bool:
    return 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31


def _fmt(d) -> str:
    (y, mo, dd), wd = d
    return f"{mo}/{dd}" + (f"({wd})" if wd else "")


def _sp(s):
    return re.sub(r"\s+", "", s)


WON = r"\d{1,3}(?:,\d{3}){2,}"          # 1,000,000 이상(원 단위)


def rent_rows_from_text(text: str) -> list[dict]:
    """표를 못 읽은 공고문(예: 옥류서원 — 머리글이 여러 줄로 쪼개짐)의 글자 줄에서 임대조건을 찾는다.
    한 줄에 원 단위 금액이 2개 이상(보증금 ≥ 100만, 월세 1만~300만)이면 한 타입으로 본다.
    전용면적은 그 줄 바로 윗줄이 소수만 있는 줄이면 그 첫 값, 아니면 줄 안의 소수 중 20 이상 첫 값."""
    out = []
    lines = (text or "").split("\n")
    for i, ln in enumerate(lines):
        nums = [int(x.replace(",", "")) for x in re.findall(WON + r"|\d{1,3}(?:,\d{3})", ln)]
        big = [x for x in nums if x >= 1_000_000]
        if not big:
            continue
        dep = big[0]
        rest = nums[nums.index(dep) + 1:]
        rent = next((x for x in rest if 10_000 <= x <= 3_000_000 and x < dep), None)
        if rent is None or not (5_000_000 <= dep <= 1_500_000_000):
            continue
        prev = lines[i - 1].strip() if i else ""
        area = ""
        if re.fullmatch(r"[\d.\s()]+", prev) and re.search(r"\d+\.\d+", prev):
            area = re.search(r"\d+\.\d+", prev).group(0)
        else:
            m = next((x for x in re.findall(r"\d+\.\d+", ln.split(f"{dep:,}")[0]) if 20 <= float(x) <= 200), "")
            area = m
        head = ln.split(f"{dep:,}")[0]
        label = re.sub(r"\b\d{2,3}\.\d{1,2}\b", "", head)          # 면적 값만 지운다('1.5룸'은 남김)
        label = re.sub(r"^\s*\d+\s+", "", label).strip()
        sup = re.search(r"(특별|일반|우선)\s*공급(\([^)]*\))?", label)      # '일반공급 102동 25세대, …' → '일반공급'
        label = sup.group(0).replace(" ", "") if sup else label[:16]
        out.append({"kind": "", "label": label, "area": area,
                    "prices": [{"ratio": "-", "deposit": round(dep / 10_000, 2), "rent": round(rent / 10_000, 2)}]})
    if not out and re.search(r"단위\s*[:：]?\s*만\s*원", text or ""):
        out = _man_rows(lines)
    return out if 1 <= len(out) <= 40 else []


def _man_rows(lines: list[str]) -> list[dict]:
    """'단위 : 만 원' 표가 칸마다 줄로 풀린 경우(청년안심주택 6806): '9,700 53' 처럼 숫자 2개만 있는 줄 = 보증금·월세.
    전용면적·타입은 위로 6줄 안의 '36.12㎡'·'(40A)'."""
    out = []
    for i, ln in enumerate(lines):
        m = re.fullmatch(r"\s*(\d{1,3}(?:,\d{3})|\d{3,6})\s+(\d{1,3}(?:\.\d+)?)\s*", ln)
        if not m:
            continue
        dep, rent = float(m.group(1).replace(",", "")), float(m.group(2))
        if not (100 <= dep <= 100_000 and 1 <= rent <= 500 and rent < dep):
            continue
        near = "\n".join(lines[max(0, i - 6):i])
        area = re.findall(r"(\d{2,3}\.\d{1,2})\s*(?:㎡|m2|m²)", near)
        label = re.findall(r"\(([0-9]{2,3}[A-Z]{0,2})\)", near)
        out.append({"kind": "", "label": label[-1] if label else "", "area": area[-1] if area else "",
                    "prices": [{"ratio": "-", "deposit": dep, "rent": rent}]})
    return out


def fill(n: dict, res: dict) -> dict:
    """읽은 결과로 빈 칸만 채운 사본. 이미 값이 있는 칸(출처가 준 값)은 건드리지 않는다."""
    import eligibility as ELIG
    from pdfparse import parse_pdf_bytes
    m = dict(n)
    how = []
    if not m.get("types"):
        for f in res["files"]:
            if f["kind"] == "pdf" and f["data"]:
                try:
                    rows, why = parse_pdf_bytes(f["data"])
                except Exception as e:
                    rows, why = [], type(e).__name__
                if rows:
                    m["types"], m["types_complete"] = rows, not why
                    how.append("임대조건: PDF 표")
                    break
        if not m.get("types"):
            rows = rent_rows_from_text(res["text"])
            if rows:
                m["types"], m["types_complete"] = rows, True
                how.append("임대조건: 공고문 글자 줄")
    if not m.get("elig"):
        try:
            m["elig"] = ELIG.summarize(res["text"]) if res["text"] else []
        except Exception:
            m["elig"] = []
    if not m.get("deadline") or m.get("deadline") == "공고문 확인":
        d = deadline_from_text(res["text"])
        if d:
            m["deadline"] = d
            how.append("마감: 공고문 일정")
    m["_read_how"] = how
    return m
