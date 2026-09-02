"""공고문 PDF에서 주택형별 임대조건(전용면적·보증금·월세)을 뽑는다.

청년안심주택 공고문은 표를 선(ruling line) 없이 그리는 경우가 많아
pdfplumber 의 extract_tables 로는 잡히지 않는다. 대신 텍스트 줄을 창(window)
단위로 훑어, '숫자 쌍이 여러 개인 줄' 주변에서 전용면적과 유형을 찾는다.

실제 서식 예 (일반공급):
    18.36
    청년 309 5,700 50 7,600 43 9,500 36
    (18A TYPE)

    신혼 36.73
    59 8,800 75 11,800 64 14,700 54
    부부 (36 TYPE)

보증금 비율 30/40/50% 세 쌍이 나열되므로, 보증금은 최소~최대,
월세는 그에 대응해 (역순으로) 최소~최대를 범위로 제시한다.
"""
from __future__ import annotations
import io, re
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; seoul-notice-bot/1.0)"}

AREA = re.compile(r"(?<!\d)(\d{2,3}\.\d{1,2})(?!\d)")
KIND = re.compile(r"(청년|신혼|일반|특별|고령|다자녀|주거약자)")
NUM = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
SECTION = "임대보증금 및 월 임대료"


def fetch_types(pdf_url: str, limit: int = 4, timeout: int = 90) -> list[dict]:
    try:
        import pdfplumber
        r = requests.get(pdf_url, headers=UA, timeout=timeout)
        r.raise_for_status()
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
    except Exception:
        return []

    target = [t for t in pages if SECTION in t] or pages
    rows: list[dict] = []
    for text in target:
        rows += _parse_text(text)
        if rows:
            break
    if not rows:                       # 섹션을 못 찾으면 전체를 훑는다
        for text in pages:
            rows += _parse_text(text)

    # 1) 같은 면적은 '일반공급'(보증금이 더 큰 쪽)을 남긴다
    best: dict[float, dict] = {}
    for row in rows:
        k = round(row["area_val"], 2)
        if k not in best or row["dep_max"] > best[k]["dep_max"]:
            best[k] = row
    # 2) 임대조건이 완전히 같은 타입(18A/18B 등)은 가장 작은 면적 하나만 남긴다
    merged: dict[tuple, dict] = {}
    for k in sorted(best):
        row = best[k]
        sig = (row["label"], row["deposit"], row["rent"])
        if sig in merged:
            merged[sig]["dup"] = merged[sig].get("dup", 1) + 1
        else:
            merged[sig] = row
    return list(merged.values())[:limit]


def _parse_text(text: str) -> list[dict]:
    lines = [ln.strip() for ln in text.split("\n")]
    # 일반공급 섹션이 있으면 그 뒤만 본다 (물량이 많고 대다수 신청자가 해당)
    for i, ln in enumerate(lines):
        if "일반공급" in ln and i < len(lines) - 5:
            lines = lines[i:]
            break

    out = []
    for i, ln in enumerate(lines):
        pairs = _pairs(ln)
        if not pairs:
            continue
        window = " ".join(lines[max(0, i - 1): i + 2])
        a = AREA.search(ln) or AREA.search(window)
        if not a:
            continue
        area = float(a.group(1))
        if not (9 <= area <= 200):     # 전용면적으로 볼 수 없는 값 제외
            continue
        k = KIND.search(ln) or KIND.search(window)
        deps = [p[0] for p in pairs]
        rents = [p[1] for p in pairs]
        out.append({
            "label": (k.group(1) if k else "공급"),
            "area": f"전용 {a.group(1)}㎡",
            "area_val": area,
            "dep_max": max(deps),
            "deposit": _rng(deps),
            "rent": _rng(rents),
        })
    return out


def _pairs(line: str) -> list[tuple[int, int]]:
    """'청년 309 5,700 50 7,600 43 9,500 36' → [(5700,50),(7600,43),(9500,36)]"""
    nums = [int(m.group(0).replace(",", "")) for m in NUM.finditer(line)]
    if len(nums) < 4:
        return []
    pairs = []
    for j in range(len(nums) - 1):
        dep, rent = nums[j], nums[j + 1]
        # 보증금은 네 자리 이상, 월세는 세 자리 이하이고 보증금보다 작다
        if 1000 <= dep <= 100000 and 5 <= rent <= 999 and rent < dep:
            pairs.append((dep, rent))
    # 인접 중복 제거 (같은 보증금이 두 번 잡히는 경우)
    uniq, seen = [], set()
    for p in pairs:
        if p[0] in seen:
            continue
        seen.add(p[0])
        uniq.append(p)
    return uniq if len(uniq) >= 2 else []


def _rng(vals: list[int]) -> str:
    lo, hi = min(vals), max(vals)
    f = lambda v: f"{v:,}"
    return f(lo) if lo == hi else f"{f(lo)}~{f(hi)}"
