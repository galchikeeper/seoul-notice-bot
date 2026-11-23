"""공고문 글자(본문·PDF·이미지 OCR)에서 자격 조건을 뽑아 한 줄로 요약한다.

판정(맞다/안 맞다)은 하지 않는다. 공고문에 보이는 항목만 정해진 문형으로 적고, 없는 항목은 뺀다.
PDF 2단 배치·OCR 잡음 때문에 원문 문장을 그대로 옮기지 않고, 정해진 패턴에서 숫자·낱말만 꺼내 쓴다.
아무것도 못 찾으면 빈 목록 → 카드에는 '자격: 공고문 확인'.
"""
from __future__ import annotations
import io, re

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; seoul-notice-bot/1.0)"}


def _ns(t: str) -> str:
    """공백·줄바꿈을 모두 지운다(OCR 이 글자마다 띄우고, PDF 는 줄을 아무 데서나 끊는다)."""
    return re.sub(r"\s+", "", t or "")


def _uniq(xs):
    out = []
    for x in xs:
        if x and x not in out:
            out.append(x)
    return out


def _money(s: str) -> str:
    """'4,542만원' · '3억4,500만원' → 그대로 보기 좋게."""
    return s.replace("만원", "만 원").replace("억", "억 ")


def summarize(text: str) -> list[str]:
    """공고문 글자 → ['나이 만19~39세', '무주택', ...]. 순서: 나이·무주택·소득·자산·차량·거주지·혼인·공급구분·입주·기타."""
    t = _ns(text)
    if len(t) < 20:
        return []
    out: list[str] = []

    # 나이 — '만 19세 이상 ~ 만 39세 이하' · '19세이상39세이하'
    ages = []
    for m in re.finditer(r"(만)?(\d{2})세이상[~∼\-]?(?:부터)?(만)?(\d{2})세이하", t):
        a, b = int(m.group(2)), int(m.group(4))
        if 15 <= a < b <= 80:
            ages.append(f"나이 {'만' if m.group(1) or m.group(3) else ''}{a}~{b}세")
    out += _uniq(ages)[:2]

    # 무주택
    if re.search(r"무주택(?:요건|자|세대|청년|조건|이어야|여야)|주택을소유하지않", t):
        whole = re.search(r"세대원(?:전부|모두)[는은가이]?무주택", t)
        out.append("무주택" + ("(세대원 전부)" if whole else ""))

    # 소득 — '월평균소득 100%' 들
    inc = _uniq(m.group(1) for m in re.finditer(r"월평균소득(?:액)?(?:의)?(\d{2,3})%", t)
                if 30 <= int(m.group(1)) <= 200)
    if inc:
        out.append("소득 월평균소득 " + "·".join(f"{x}%" for x in inc[:4]) + " 이하"
                   + (" (유형별)" if len(inc) > 1 else ""))
    elif re.search(r"소득기준[–\-:/]|소득기준에따라|소득\d분위기준|소득기준을", t):
        out.append("소득 기준 있음(금액은 공고문)")

    # 자산 — '총자산 3억4,500만원 이하'
    ast = _uniq(m.group(1) for m in re.finditer(
        r"(?:총자산|보유자산|자산(?:가액|총액|기준)?)(?:이|은|는|합계|기준)?[가-힣]{0,4}?"
        r"(\d+억(?:[\d,]+만)?원|[\d,]{3,}만원)(?:이하|이내)", t))
    if ast:
        out.append("자산 " + "·".join(_money(x) for x in ast[:2]) + " 이하")
    else:
        # 청년안심주택: '청년(본인) 자산기준은 30~39세 가구 금액' · '신혼부부 세대 자산기준은 소득3분위 금액' (금액은 표)
        a1 = re.search(r"청년\(본인\)자산기준은(\d{2}~\d{2})세가구", t)
        a2 = re.search(r"신혼부부\(구성될\)세대자산기준은소득(\d)분위", t)
        if a1 or a2:
            out.append("자산 기준: " + ", ".join(x for x in (
                f"청년 {a1.group(1)}세 가구 기준" if a1 else "", f"신혼 소득{a2.group(1)}분위 기준" if a2 else "") if x)
                + "(금액은 공고문)")
        elif re.search(r"자산기준[–\-:/]|자산기준에따라|자산기준을", t):
            out.append("자산 기준 있음(금액은 공고문)")

    # 차량 — '기준자동차가액 4,542만원 이내' · '차량기준가액 4,542만원(이륜차포함) 이하'
    car = _uniq(m.group(1) for m in re.finditer(
        r"(?:자동차|차량)[가-힣]{0,6}?([\d,]{3,}만원)(?:\([^)]{0,10}\))?(?:이하|이내|미만)", t))
    if car:
        out.append("차량가액 " + "·".join(_money(x) for x in car[:2]) + " 이하")
    elif re.search(r"(?:자동차|차량)[가-힣()]{0,12}?(?:소유|보유)(?:가)?불가|무소유", t):
        out.append("차량 소유 불가")
    elif re.search(r"자동차가액기준[을/]|자동차가액기준을", t):
        out.append("차량가액 기준 있음(금액은 공고문)")

    # 거주지 우선순위
    res = _uniq(m.group(1) for m in re.finditer(
        r"(서울(?:특별)?시|[가-힣]{1,4}(?:구|시|군))(?:에)?(?:거주(?:하는|자|중인)?)(?:를|에게)?(?:[가-힣]{0,4})?우선", t))
    must = _uniq(m.group(1) for m in re.finditer(r"(서울(?:특별)?시|[가-힣]{1,4}(?:구|시|군))에거주하는", t))
    if must:
        out.append("거주: " + "·".join(must[:2]) + " 거주자")
    if res:
        out.append("거주지 우선: " + "·".join(res[:3]))
    else:
        r1 = re.search(r"1순위(?:지역)?(?:서울특별시|서울시)([가-힣]{1,4}구)(?:\d+호)?거주", t)
        if r1:
            same = "동일소득일경우지역순위" in t
            out.append(f"거주지 1순위: {r1.group(1)} 거주자" + (" (소득이 같을 때)" if same else ""))

    # 혼인·신혼
    if re.search(r"예비신혼부부[^※■]{0,30}?(?:입주일?|입주전|계약일?)까지혼인신고", t):
        out.append("예비신혼부부는 입주 전까지 혼인신고")
    elif "예비신혼부부" in t:
        out.append("신혼부부(예비 포함) 신청 가능")
    elif "신혼부부" in t:
        out.append("신혼부부 신청 가능")

    # 특별/일반 공급 구분
    sp, gen = "특별공급" in t, "일반공급" in t
    if sp or gen:
        out.append("공급: " + "·".join(x for x, y in (("특별", sp), ("일반", gen)) if y))

    # 입주 시기
    m = re.search(r"(\d{4})년(\d{1,2})월(\d{1,2})일(?:이전|까지)입주", t) or \
        re.search(r"(?<!\d)(\d{2})\.(\d{1,2})\.(\d{1,2})\.?(?:이전|까지)입주", t)
    if m:
        out.append(f"{int(m.group(2))}/{int(m.group(3))}까지 입주 가능해야 함")
    m = re.search(r"계약(?:체결|결)?일로부터(\d{1,2})(주|개월|일)이내(?:에)?입주", t)
    if m:
        out.append(f"계약일부터 {m.group(1)}{m.group(2)} 이내 입주")

    # 기타 제한
    lim = []
    # '임대사업자 문제로 … 대출 불가 시' 처럼 다른 뜻의 '사업자'는 빼고, 문장(…니다)을 넘어가지 않는다
    for w, label in (("법인", "법인"), ("(?<!임대)사업자", "사업자"), ("외국인", "외국인"), ("반려", "반려동물")):
        if re.search(w + r"(?:(?!니다)[^※■。]){0,20}?(?:불가|금지|불허)(?!능또는|시)", t):
            lim.append(label)
    if lim:
        out.append("·".join(lim) + " 불가")
    # '대출이 불가능 또는 일부 제한될 수' 같은 조건문은 빼고, '불가합니다'로 끝맺은 규정만
    if re.search(r"대출(?:신청)?(?:\([^)]{0,30}\))?(?:이|은)?불가(?:능)?합니다", t):
        out.append("대출 불가")
    if re.search(r"여성(?:전용|만)", t):
        out.append("여성만 입주")
    if re.search(r"남성(?:전용|만)", t):
        out.append("남성만 입주")
    return out


def line(items: list[str]) -> str:
    return "자격: " + (" · ".join(items) if items else "공고문 확인")


# ─────────────────────────── 출처별 글자 가져오기 ───────────────────────────

def pdf_text(url: str, pages: int = 12, timeout: int = 90) -> str:
    import pdfplumber
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages[:pages])


def html_text(url: str, timeout: int = 30) -> str:
    from bs4 import BeautifulSoup
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for x in soup(["script", "style", "nav", "header", "footer"]):
        x.decompose()
    return soup.get_text("\n", strip=True)


def image_text(images: list[bytes]) -> str:
    """엘리스 공고문 이미지 전체 OCR(쪽 단위). '온라인 계약신청 방법 안내' 쪽은 자격 정보가 없어 건너뛴다."""
    import elyes_ocr as OCR
    if not OCR.available():
        return ""
    from PIL import Image
    out = []
    for b in images[:4]:
        try:
            im = Image.open(io.BytesIO(b)).convert("L")
        except Exception:
            continue
        im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        t = OCR._ocr(im, "kor+eng", 6)
        if "계약신청방법안내" in _ns(t)[:80]:
            continue
        out.append(t)
    return "\n".join(out)
