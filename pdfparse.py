"""공고문 PDF의 [공급현황] 표에서 타입별 임대조건(전용면적·보증금 비율·보증금·월세)을 읽는다.

원칙: 표에서 읽은 값만 쓴다. 표를 못 찾거나 값 검사에 걸리면 빈 목록을 돌려주고,
부르는 쪽은 "확인 실패"로 표시한다. 본문 문장에서 숫자를 주워 채우지 않는다.
(예전 방식은 1쪽 안내문 '소액임차인기준금액 …('23.02.21~)' 문장에서 날짜를 전용면적으로,
 6,500·5,000 을 보증금으로 읽어 모든 공고에 23.02㎡ · 5,000~6,500만원을 붙였다.)

단위: 머리글의 '(단위 : 원)' 표기는 틀린 공고가 있어 믿지 않고 숫자 크기로 판단한다.
  보증금이 모두 100만 이상이고 월세가 모두 1만 이상 → 원 단위 → 만원으로 환산
  보증금이 모두 10만 이하이고 월세가 모두 500 이하   → 만원 단위
  그 밖(섞였거나 애매함) → 그 표는 확인 실패
"""
from __future__ import annotations
import io, re
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; seoul-notice-bot/1.0)"}

MONEY = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
RATIO = re.compile(r"(\d{1,3}(?:\s*~\s*\d{1,3})?)\s*%")
AREA_UNIT = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*(?:㎡|m2|m²|㎥)")
AREA_DEC = re.compile(r"^\s*(\d{1,3}\.\d{1,2})(?!\d)")
SKIP_ROW = re.compile(r"^(합\s*계|소\s*계|계)$")


def _clean(s) -> str:
    s = re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()
    toks = s.split(" ")
    # '특 별 공 급' 처럼 글자마다 띄운 칸은 붙인다
    if len(toks) >= 3 and sum(len(t) == 1 for t in toks) / len(toks) > 0.6:
        s = "".join(toks)
    return s


def _num(s: str) -> float | None:
    m = MONEY.search((s or "").replace("\\", "").replace("₩", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _header_rows(t: list[list]) -> int:
    k = 1
    while k < min(3, len(t)):
        row = [_clean(c) for c in t[k]]
        if any(re.search(r"보증금|임대료|월세", c) for c in row) and \
           not any(re.fullmatch(r"[\d,\\.]{4,}", c.replace("\\", "")) for c in row):
            k += 1
        else:
            break
    return k


def _columns(t: list[list], k: int) -> dict | None:
    width = max(len(r) for r in t)
    top = [_clean(c) for c in t[0]] + [""] * (width - len(t[0]))
    filled, last = [], ""
    for c in top:                       # 가로로 합쳐진 칸은 왼쪽 머리글을 이어 받는다
        last = c or last
        filled.append(last)
    subs = [" ".join(_clean(t[r][i]) if i < len(t[r]) else "" for r in range(1, k)) for i in range(width)]
    labels = [f"{filled[i]} {subs[i]}".strip() for i in range(width)]

    area = next((i for i, l in enumerate(labels) if "전용" in l), None)
    if area is None:
        return None
    group = [i for i, l in enumerate(labels) if "유형" in l and i != area]
    money = []
    for i, l in enumerate(labels):
        if i == area or i in group or re.search(r"기존|참고|근저당|보증보험", l):
            continue
        sub = subs[i] if k > 1 else top[i]
        if re.search(r"보증금", sub) and not re.search(r"임대료|월세", sub):
            money.append(("dep", i))
        elif re.search(r"임대료|월세", sub):
            money.append(("rent", i))
    pairs = []
    for j, (kind, i) in enumerate(money):
        if kind == "dep" and j + 1 < len(money) and money[j + 1][0] == "rent":
            r = RATIO.search(filled[i])
            pairs.append((i, money[j + 1][1], (r.group(1).replace(" ", "") + "%") if r else "기준"))
    if not pairs:
        return None
    return {"area": area, "group": group, "pairs": pairs}


def _area_label(cell: str) -> tuple[str, str]:
    """('20.20', '25형') — 면적을 못 읽으면 면적은 ''."""
    c = _clean(cell)
    m = AREA_UNIT.search(c) or AREA_DEC.search(c)
    area = m.group(1) if m else ""
    paren = re.search(r"\(([^)]*)\)?", c)
    if paren and paren.group(1).strip():
        label = paren.group(1).strip()
    elif m:
        label = _clean(c[:m.start()] + c[m.end():]).strip("() ")
    else:
        label = c
    return area, label


def parse_table(t: list[list]) -> tuple[list[dict], str]:
    """표 1개 → (타입 목록, 실패 사유). 성공이면 사유는 ''."""
    if not t or len(t) < 2:
        return [], "표 아님"
    k = _header_rows(t)
    cols = _columns(t, k)
    if not cols:
        return [], "머리글에 전용·보증금·임대료 칸 없음"
    out, carry = [], {}
    for row in t[k:]:
        cells = [_clean(c) for c in row] + [""] * 30
        if any(SKIP_ROW.match(cells[i]) for i in [0, *cols["group"]]):
            continue
        for g in cols["group"]:          # 세로로 합쳐진 칸은 윗줄 값을 이어 받는다
            if cells[g]:
                carry[g] = cells[g]
        prices = []
        for di, ri, ratio in cols["pairs"]:
            d, r = _num(cells[di]), _num(cells[ri])
            if d is None and r is None:
                continue
            if d is None or r is None:
                return [], f"보증금·월세 한쪽만 있음({cells[di]!r}/{cells[ri]!r})"
            prices.append({"ratio": ratio, "deposit": d, "rent": r})
        if not prices:
            continue                      # 호수만 적힌 이어지는 줄 등
        acell = cells[cols["area"]]
        if acell:
            carry["area"] = acell
        elif "area" in carry:
            acell = carry["area"]            # 세로로 합쳐진 타입 칸(같은 타입의 다른 호실 줄) — 윗줄 타입을 이어 받는다
        area, label = _area_label(acell)
        kind = " ".join(dict.fromkeys(carry[g] for g in cols["group"] if carry.get(g)))
        out.append({"kind": kind, "label": label, "area": area, "prices": prices})
    if not out:
        return [], "값이 있는 줄 없음"

    deps = [p["deposit"] for o in out for p in o["prices"]]
    rents = [p["rent"] for o in out for p in o["prices"]]
    if all(d >= 1_000_000 for d in deps) and all(r >= 10_000 for r in rents):
        for o in out:
            for p in o["prices"]:
                p["deposit"] = round(p["deposit"] / 10_000, 2)
                p["rent"] = round(p["rent"] / 10_000, 2)
    elif not (all(100 <= d <= 100_000 for d in deps) and all(1 <= r <= 500 for r in rents)):
        return [], f"단위 판별 실패(보증금 {min(deps):g}~{max(deps):g}, 월세 {min(rents):g}~{max(rents):g})"
    for o in out:
        for p in o["prices"]:
            if not (100 <= p["deposit"] <= 100_000 and 1 <= p["rent"] <= 500 and p["rent"] < p["deposit"]):
                return [], f"값 범위 밖({p['deposit']:g}/{p['rent']:g})"
    return out, ""


def parse_pdf_bytes(data: bytes) -> tuple[list[dict], str]:
    import pdfplumber
    out, reasons = [], []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for tb in page.find_tables():
                t = tb.extract()
                head = " ".join(_clean(c) for r in t[:3] for c in r)
                if not ("전용" in head and "보증금" in head and re.search(r"임대료|월세", head)):
                    continue
                rows, why = parse_table(t)
                if why:
                    reasons.append(why)
                # 표가 특별공급·일반공급으로 나뉜 공고는 구분이 표 바로 위 제목에만 있다
                x0, top, x1, _ = tb.bbox
                above = page.crop((x0, max(0, top - 70), x1, top)).extract_text() or ""
                cap = re.findall(r"^\s*(?:\(\d\)\s*)?(특별\s*공급|일반\s*공급)\s*$", above, re.M)
                for r in rows:
                    if cap and not re.search(r"특별|일반", r["kind"]):
                        r["kind"] = (r["kind"] + " " + cap[-1].replace(" ", "")).strip()
                for r in rows:
                    if r not in out:
                        out.append(r)
    # 일부 표만 읽혔으면 행과 함께 실패 사유도 돌려준다(부르는 쪽이 '전부 읽음'으로 오해하지 않게)
    if out:
        return out, "; ".join(reasons)
    return [], ("; ".join(reasons) if reasons else "공급 표를 찾지 못함")


def fetch_types(pdf_url: str, timeout: int = 90, with_status: bool = False):
    """PDF 주소 → 타입 목록. 실패하면 [] (부르는 쪽에서 '확인 실패').
    with_status=True 면 (목록, 전부 읽었는지) 를 돌려준다."""
    try:
        r = requests.get(pdf_url, headers=UA, timeout=timeout)
        r.raise_for_status()
        rows, why = parse_pdf_bytes(r.content)
    except Exception as e:
        print(f"pdf: 읽기 실패 ({type(e).__name__})")
        return ([], False) if with_status else []
    if why:
        print(f"pdf: 표 확인 실패{'(일부)' if rows else ''} — {why[:120]}")
    return (rows, not why) if with_status else rows


def fmt_num(v: float) -> str:
    return f"{int(v):,}" if float(v).is_integer() else f"{v:,.2f}".rstrip("0").rstrip(".")


def type_lines(types: list[dict], limit_chars: int = 2300) -> str:
    """슬랙 카드용 줄 목록. 줄바꿈만 써서 폰에서도 정렬이 깨지지 않게 한다."""
    if not types:
        return "평형·보증금·월세: 확인 실패 (공고문 확인)"
    lines = ["타입별 조건 (만원 · 보증금/월세)"]
    shown = 0
    for t in types:
        name = t["label"] or "타입"
        head = f"• {name}" + (f" · 전용 {t['area']}㎡" if t["area"] else " · 전용면적 확인 실패")
        if t["kind"]:
            head += f" · {t['kind']}"
        body = "    " + " · ".join(f"{p['ratio']} {fmt_num(p['deposit'])}/{fmt_num(p['rent'])}"
                                   for p in t["prices"])
        if len("\n".join(lines)) + len(head) + len(body) + 40 > limit_chars:
            break
        lines += [head, body]
        shown += 1
    if shown < len(types):
        lines.append(f"외 {len(types) - shown}개 타입 · 공고문 확인")
    return "\n".join(lines)
