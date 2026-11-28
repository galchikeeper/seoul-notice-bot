"""롯데 엘리스 공고문 이미지에서 임대조건·신청 일정 읽기.

엘리스 모집공고 본문은 글자가 아니라 공고문 이미지(JPG)다. 이미지 속 표의 테두리 선을 찾아
칸을 나누고, 칸 하나씩 tesseract(한국어)로 읽는다. 쪽 전체를 한 번에 읽으면 표가 뒤섞여 못 쓴다.

못 읽거나 값이 말이 안 되면 지어내지 않고 빈 값으로 돌려준다(카드에는 '공고문 확인').
tesseract 가 없으면(로컬 등) 아무것도 읽지 않는다.
"""
from __future__ import annotations
import io, re, shutil, subprocess, datetime as dt

try:
    import numpy as np
    from PIL import Image, ImageOps
except Exception:                      # pragma: no cover — 의존성이 없으면 OCR 을 건너뛴다
    np = None

DARK = 110          # 이 밝기보다 어두우면 선·글자로 본다(바깥 회색 틀 ≈128 은 제외)
MIN_HLINE = 150     # 가로선으로 볼 최소 길이(px, 원본 794px 폭 기준)


def available() -> bool:
    return np is not None and shutil.which("tesseract") is not None


# ─────────────────────────── 표 찾기 ───────────────────────────

def _runs(row) -> list[tuple[int, int]]:
    """True 가 이어진 구간들 [(시작, 끝)]."""
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _cluster(vals: list[int], gap: int = 2) -> list[list[int]]:
    out: list[list[int]] = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= gap:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def find_tables(g) -> list[list[list[tuple[int, int, int, int]]]]:
    """회색조 배열 → 표 목록. 표 = 행 목록, 행 = 칸 (x0, y0, x1, y1) 목록."""
    dark = g < DARK
    h, w = dark.shape
    hl = []                                         # (y, x0, x1)
    for y in range(h):
        for a, b in _runs(dark[y]):
            if b - a >= MIN_HLINE:
                hl.append((y, a, b))
    # 붙어 있는 가로선 줄(두께 1~3px)을 하나로
    lines = []
    for grp in _cluster(sorted({y for y, _, _ in hl})):
        segs = [(a, b) for y, a, b in hl if y in grp]
        lines.append((grp[0], grp[-1], min(a for a, _ in segs), max(b for _, b in segs)))
    bands = []
    for (ya0, ya1, xa0, xa1), (yb0, yb1, xb0, xb1) in zip(lines, lines[1:]):
        if not 14 <= yb0 - ya1 <= 260:
            continue
        x0, x1 = max(xa0, xb0), min(xa1, xb1)
        if x1 - x0 < MIN_HLINE:
            continue
        sub = dark[ya1 + 2:yb0 - 1, max(0, x0 - 3):x1 + 3]
        cols = np.where(sub.mean(axis=0) >= 0.9)[0] + max(0, x0 - 3)
        vx = [c[0] for c in _cluster(list(cols))]
        if len(vx) < 3:
            continue
        cells = [(a + 2, ya1 + 2, b - 1, yb0 - 1) for a, b in zip(vx, vx[1:]) if b - a > 12]
        if len(cells) >= 2:
            bands.append(((ya0, ya1), (yb0, yb1), cells))
    tables: list[list] = []
    for top, bot, cells in bands:
        if tables and tables[-1][-1][1] == top and len(tables[-1][-1][2]) == len(cells):
            tables[-1].append((top, bot, cells))
        else:
            tables.append([(top, bot, cells)])
    return [[cells for _, _, cells in t] for t in tables]


# ─────────────────────────── 칸 읽기 ───────────────────────────

def _ocr(img, lang: str = "kor+eng", psm: int = 6, whitelist: str = "") -> str:
    cmd = ["tesseract", "stdin", "stdout", "-l", lang, "--psm", str(psm)]
    if whitelist:
        cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    try:
        r = subprocess.run(cmd, input=buf.getvalue(), capture_output=True, timeout=30)
    except Exception:
        return ""
    return r.stdout.decode("utf-8", "ignore").strip()


def _cell_img(im, box, scale: int = 3):
    x0, y0, x1, y1 = box
    c = im.crop((x0 + 1, y0 + 1, x1 - 1, y1 - 1))
    c = c.resize((max(1, c.width * scale), max(1, c.height * scale)), Image.LANCZOS)
    return ImageOps.expand(c, border=24, fill=255)


def read_cell(im, box, kind: str = "text") -> str:
    c = _cell_img(im, box)
    if kind == "money":
        return _ocr(c, "eng", 7, "0123456789,")
    if kind == "area":
        return _ocr(c, "eng", 7, "0123456789.")
    if kind == "code":
        return _ocr(c, "eng", 7, "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-")
    return _ocr(c, "kor+eng", 6)


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


# ─────────────────────────── 값 해석 ───────────────────────────

RENT_FIELDS = [            # (필드, 머리 칸 글자 패턴) — 공고마다 열 순서가 달라 머리 글자로 찾는다
    ("area", r"전용|면적"),
    ("type", r"타입|^타|입$"),
    ("deposit", r"보[증중정]금|증금"),
    ("rent", r"임[대데]료|월임|월세"),
    ("floor", r"^[층충]|[층충]수|[층충]$"),          # '층' 한 글자는 '충'으로 읽히곤 한다
    ("dir", r"방향|향$"),
    ("use", r"용도"),
    ("units", r"세대수"),
    ("note", r"비고"),
]
SCHED_FIELDS = [("apply", r"신청기간|신청"), ("winner", r"당첨"), ("contract", r"계약체결|체결")]


def _map_head(texts: list[str], fields) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, t in enumerate(texts):
        t = _nospace(t)
        for f, pat in fields:
            if f not in out and re.search(pat, t):
                out[f] = i
                break
    return out


def money(s: str) -> int | None:
    """'10,000,000' → 10000000 · '1,500만' → 15000000 · '1억 9,900만' → 199000000 (원)."""
    t = re.sub(r"(?<=\d)[.,]{2,}(?=\d)", ",", _nospace(s))       # 쉼표+점이 겹쳐 읽힘: '2,.000만' (어바니엘 한강 2026-11-27)
    t = re.sub(r"(?<=\d)\.(?=\d{3}(?!\d))", ",", t)           # OCR 이 쉼표를 마침표로 읽는다: '1.500만'
    m = re.fullmatch(r"(?:(\d+)억)?(?:([\d,]+)만)?원?", t)
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 100_000_000 + int((m.group(2) or "0").replace(",", "")) * 10_000
    v = re.sub(r"[^\d]", "", t)
    return int(v) if v and re.fullmatch(r"[\d,]+원?", t) else None


RAW: list[str] = []          # 검증 실패 때 로그로 남길 금액 칸 원문(공개 공고문 숫자뿐)


def read_money(im, box) -> int | None:
    """숫자 전용으로 먼저 읽고, 값이 이상하면('1,500만'처럼 한글 단위) 한글 포함으로 다시 읽는다."""
    a = read_cell(im, box, "money")
    v = money(a)
    b = ""
    if v is None or v < 10_000 or v % 100:
        b = read_cell(im, box, "text")
        v = money(b)
    RAW.append(f"{a!r}→{b!r}" if b else repr(a))
    return v


DIRS = ["남동", "남서", "북동", "북서", "동남", "서남", "동북", "서북", "남", "동", "서", "북"]
USES = ["오피스텔", "도생", "도시형생활주택", "아파트", "생활숙박", "기숙사"]


def _pick(s: str, vocab: list[str], suffix: str = "") -> str:
    t = _nospace(s)
    for v in vocab:
        if v in t:
            return v + (suffix if suffix and not v.endswith(suffix) and len(v) == 1 else "")
    return ""


def _note(s: str) -> str:
    """비고 칸: 깨끗하게 읽힌 한글 문구만(영문 대문자가 섞이면 OCR 잡음). '주의사항 필독'은 뺀다."""
    t = re.sub(r"\s+", " ", s or "").strip()
    t = re.sub(r"(?<=[가-힣]) (?=[가-힣])", "", t)
    if not t or re.search(r"[A-Za-z]", t) or "주의사항" in t or len(re.findall(r"[가-힣]", t)) < 2:
        return ""
    return t[:20]


def parse_rent_rows(rows: list[list[str]], col: dict[str, int]) -> list[dict]:
    out = []
    for r in rows:
        def get(f):
            return r[col[f]] if f in col and col[f] < len(r) else ""
        dep, rent = get("deposit"), get("rent")
        dep = dep if isinstance(dep, int) or dep is None else money(dep)
        rent = rent if isinstance(rent, int) or rent is None else money(rent)
        if dep is None and rent is None:
            continue
        typ = re.sub(r"[^0-9A-Z\-]", "", get("type").upper())
        fl = re.search(r"\d{1,2}", get("floor"))
        ar = re.fullmatch(r"\d{1,3}\.\d{1,2}", _nospace(get("area")))
        out.append({
            "type": typ, "deposit": dep, "rent": rent,
            "area": ar.group() if ar and 5 <= float(ar.group()) <= 200 else "",
            "floor": f"{int(fl.group())}층" if fl and 0 < int(fl.group()) < 80 else "",
            "dir": _pick(get("dir"), DIRS, "향"),
            "use": _pick(get("use"), USES),
            "units": (re.search(r"\d{1,3}", get("units")) or [""])[0] if "units" in col else "",
            "note": _note(get("note")),
        })
    return out


def plausible(row: dict) -> bool:
    """원 단위 보증금·월세가 말이 되는지. (OCR 이 자릿수를 빠뜨리면 여기서 걸러진다)"""
    d, r = row.get("deposit"), row.get("rent")
    return (d is not None and 1_000_000 <= d <= 2_000_000_000 and d % 10_000 == 0
            and r is not None and 50_000 <= r <= 5_000_000 and r % 100 == 0)


DT_RE = re.compile(r"(20\d{2}|\d{2})[.\-](\d{1,2})[.\-](\d{1,2})")    # 2026.11.20 · 26.11.20
TM_RE = re.compile(r"(\d{1,2})(?::(\d{2})|시)")


def parse_when(s: str) -> tuple[dt.datetime | None, dt.datetime | None]:
    """'2026.11.20(금) 18시 ~ 2026.11.22(일) 18시' · '2026.11.23(월) 09시 ~ 18시' → (시작, 끝).
    시각이 없으면 시작은 0시, 끝은 23:59 로 본다."""
    t = _nospace(s).replace("오전", "").replace("오후", "PM")
    parts = re.split(r"[~∼〜]", t, maxsplit=1)

    def one(p, last_date):
        dm = DT_RE.search(p)
        d = None
        if dm:
            try:
                y = int(dm.group(1))
                d = dt.date(y + 2000 if y < 100 else y, int(dm.group(2)), int(dm.group(3)))
            except ValueError:
                d = None
            p = p[dm.end():]
        d = d or last_date
        tm = TM_RE.search(p)
        hh = mm = None
        if tm and 0 <= int(tm.group(1)) <= 24:
            hh, mm = int(tm.group(1)), int(tm.group(2) or 0)
        return d, hh, mm

    d0, h0, m0 = one(parts[0], None)
    start = dt.datetime.combine(d0, dt.time(h0 % 24, m0)) if d0 and h0 is not None else (
        dt.datetime.combine(d0, dt.time(0, 0)) if d0 else None)
    if len(parts) < 2:
        return start, None
    d1, h1, m1 = one(parts[1], d0)
    if not d1:
        return start, None
    end = dt.datetime.combine(d1, dt.time(23, 59) if h1 is None or h1 == 24 else dt.time(h1, m1))
    return start, end


def read_images(images: list[bytes]) -> dict:
    """공고문 이미지들 → {'rows': [...], 'apply': (시작, 끝), 'winner': 시각, 'contract': (시작, 끝), 'errors': [...]}.
    rows 의 보증금·월세는 원 단위 정수. 읽지 못한 값은 빈 값."""
    out = {"rows": [], "apply": (None, None), "winner": None, "contract": (None, None),
           "raw_apply": "", "errors": []}
    if not available():
        out["errors"].append("tesseract 없음")
        return out
    RAW.clear()
    for b in images:
        try:
            im = Image.open(io.BytesIO(b)).convert("L")
        except Exception as e:
            out["errors"].append(f"이미지 열기 실패({type(e).__name__})")
            continue
        g = np.asarray(im)
        for t in find_tables(g):
            head = [read_cell(im, c) for c in t[0]]
            rc = _map_head(head, RENT_FIELDS)
            if not out["rows"] and "deposit" in rc and "rent" in rc:
                money_cols = {rc["deposit"], rc["rent"]}
                body = [[read_money(im, c) if i in money_cols else
                         read_cell(im, c, "code" if i == rc.get("type") else "area" if i == rc.get("area") else "text")
                         if i in rc.values() else ""
                         for i, c in enumerate(row)] for row in t[1:]]
                out["rows"] = parse_rent_rows(body, rc)
                continue
            sc = _map_head(head, SCHED_FIELDS)
            if out["apply"] == (None, None) and "apply" in sc and len(t) >= 2:
                vals = {f: read_cell(im, t[1][i]) for f, i in sc.items() if i < len(t[1])}
                out["raw_apply"] = vals.get("apply", "")
                out["apply"] = parse_when(vals.get("apply", ""))
                if vals.get("winner"):
                    out["winner"] = parse_when(vals["winner"])[0]
                if vals.get("contract"):
                    out["contract"] = parse_when(vals["contract"])
    if not out["rows"]:
        out["errors"].append("임대조건 표 못 읽음")
    elif not all(plausible(r) for r in out["rows"]):
        out["errors"].append("임대조건 값 검증 실패 — 읽은 금액 칸: " + ", ".join(RAW)[:200])
    if out["apply"][1] is None:
        out["errors"].append("신청기간 못 읽음")
    return out
