"""마이홈 공공주택 모집공고 API(data.go.kr 15108420) 1회 호출 점검 — 읽기 전용.

인증키는 env MYHOME_API_KEY 에서만 읽고 어디에도 출력하지 않는다.
(요청 주소에 키가 들어가므로 예외 메시지·응답 URL 도 출력하지 않는다)
종료 코드: 0 응답 받음 · 3 등록되지 않은 키(발급 직후) · 1 그 밖의 실패
"""
from __future__ import annotations
import collections, json, os, sys
from urllib.parse import quote
import requests

BASE = "https://apis.data.go.kr/1613000/HWSPR02"
KEY = os.environ.get("MYHOME_API_KEY", "").strip()


def call(op: str, **params) -> tuple[int, dict | None, str]:
    # 키는 받은 그대로 보낸다. 이미 %-인코딩된 키(인코딩 키)는 다시 인코딩하지 않고, 아니면(디코딩 키) 한 번 인코딩한다.
    k = KEY if "%" in KEY else quote(KEY, safe="")
    qs = "&".join(f"{a}={quote(str(b), safe='')}" for a, b in params.items())
    try:
        r = requests.get(f"{BASE}/{op}?serviceKey={k}&{qs}", timeout=40)
    except Exception as e:
        return 0, None, f"연결 오류 {type(e).__name__}"
    try:
        j = r.json()
    except ValueError:
        t = r.text
        for code in ("SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "NO_OPENAPI_SERVICE_ERROR", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"):
            if code in t:
                return r.status_code, None, code
        return r.status_code, None, f"JSON 아님 ({len(t)}B)"
    hdr = (j.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader") or {}
    if hdr.get("errMsg"):
        return r.status_code, None, hdr["errMsg"]
    return r.status_code, j, ""


def items_of(j: dict) -> tuple[list[dict], int | None]:
    """응답 모양이 문서에 없어 흔한 모양을 모두 받아 본다."""
    for path in (("response", "body", "item"), ("response", "body", "items", "item"), ("response", "body", "items"), ("hsList",), ("dsList",), ("items",)):
        x = j
        for p in path:
            x = x.get(p) if isinstance(x, dict) else None
        if isinstance(x, list):
            body = (j.get("response") or {}).get("body") or {}
            return x, body.get("totalCount") or j.get("totalCount")
        if isinstance(x, dict):
            return [x], 1
    return [], None


def main() -> int:
    if not KEY:
        print("myhome: MYHOME_API_KEY 없음"); return 1
    print(f"myhome: 키 형태 = {'인코딩 키(%포함)' if '%' in KEY else '디코딩 키'} · 길이 {len(KEY)}")
    code, j, err = call("rsdtRcritNtcList", brtcCode="11", numOfRows="100", pageNo="1")
    print(f"myhome: 공공임대 조회 HTTP {code} {err or 'OK'}")
    if err.startswith("SERVICE_KEY_IS_NOT_REGISTERED"):
        return 3
    if err:
        return 1
    print("myhome: 응답 최상위 키", list(j.keys())[:10])
    items, total = items_of(j)
    print(f"myhome: 서울 공공임대 totalCount={total} · 이번 쪽 {len(items)}건")
    if not items:
        print("myhome: 항목을 찾지 못함 — 응답 일부:", json.dumps(j, ensure_ascii=False)[:600]); return 1
    print("myhome: 항목 필드", sorted(items[0].keys()))
    allitems = list(items)
    pg = 2
    while total and len(allitems) < int(total) and pg <= 10:
        c2, j2, e2 = call("rsdtRcritNtcList", brtcCode="11", numOfRows="100", pageNo=str(pg))
        if e2:
            print(f"myhome: {pg}쪽 실패 {e2}"); break
        more, _ = items_of(j2); allitems += more; pg += 1
        if not more:
            break
    def g(x, *ks):
        return next((str(x[k]) for k in ks if x.get(k) not in (None, "")), "")
    months = collections.Counter(g(x, "rcritPblancDe", "pblancDe")[:6] for x in allitems)
    latest = max(m for m in months if m) if any(months) else ""
    print(f"myhome: 받은 서울 공고 {len(allitems)}건 · 모집공고월별 {dict(sorted(months.items(), reverse=True)[:6])}")
    by_type = collections.Counter(f"{g(x,'suplyTyNm')}({g(x,'suplyTy')})" for x in allitems)
    print("myhome: 공급유형별(받은 전체)", dict(by_type.most_common()))
    lat = [x for x in allitems if g(x, "rcritPblancDe", "pblancDe").startswith(latest)]
    print(f"myhome: 최근 모집공고월 {latest} 공급유형별", dict(collections.Counter(g(x, 'suplyTyNm') for x in lat).most_common()))
    for x in lat[:3]:
        print("myhome: 샘플 |", g(x, "rcritPblancDe"), "|", g(x, "suplyInsttNm"), "|", g(x, "suplyTyNm"), "|",
              g(x, "houseTyNm"), "|", g(x, "pblancNm")[:60], "|", g(x, "signguNm", "fullAdres", "brtcNm"), "|", g(x, "url", "pcUrl")[:90])
    priv = [x for x in allitems if "민간" in g(x, "suplyTyNm") or "청년안심" in g(x, "pblancNm") or "공공지원" in g(x, "suplyTyNm")]
    print(f"myhome: 공공지원민간임대·청년안심주택 관련 {len(priv)}건", [g(x, "pblancNm")[:40] for x in priv[:5]])
    for x in allitems:
        print("myhome: 서울 항목 상태 |", g(x, "sttusNm"), "| 모집공고일", g(x, "rcritPblancDe"), "| 접수", g(x, "beginDe"), "~", g(x, "endDe"))
    # 전국(지역 제한 없음) — 공급유형 종류와 공공지원민간임대 포함 여부
    nat, pg, ntotal = [], 1, None
    while pg <= 10:
        c4, j4, e4 = call("rsdtRcritNtcList", numOfRows="100", pageNo=str(pg))
        if e4:
            print(f"myhome: 전국 {pg}쪽 실패 {e4}"); break
        more, ntotal = items_of(j4)
        nat += more; pg += 1
        if not more or (ntotal and len(nat) >= int(ntotal)):
            break
    print(f"myhome: 전국 totalCount={ntotal} · 받은 {len(nat)}건")
    print("myhome: 전국 공급유형별", dict(collections.Counter(g(x, "suplyTyNm") for x in nat).most_common()))
    print("myhome: 전국 상태별", dict(collections.Counter(g(x, "sttusNm") for x in nat).most_common()))
    print("myhome: 전국 지역별", dict(collections.Counter(g(x, "brtcNm") for x in nat).most_common(8)))
    gg = [x for x in nat if g(x, "brtcNm").startswith("경기")]
    print(f"myhome: 경기도 {len(gg)}건 · 시군구별", dict(collections.Counter(g(x, "signguNm") for x in gg).most_common()))
    print("myhome: 경기도 공급유형별", dict(collections.Counter(g(x, "suplyTyNm") for x in gg).most_common()))
    for x in gg[:3]:
        print("myhome: 경기 샘플 |", g(x, "rcritPblancDe"), "|", g(x, "suplyInsttNm"), "|", g(x, "suplyTyNm"), "|",
              g(x, "signguNm"), "|", g(x, "pblancNm")[:50], "| 접수", g(x, "beginDe"), "~", g(x, "endDe"))
    ends = sorted(g(x, "endDe") for x in nat if g(x, "endDe"))
    print(f"myhome: 전국 접수 마감일 범위 {ends[0] if ends else '-'} ~ {ends[-1] if ends else '-'}")
    npriv = [x for x in nat if "민간" in g(x, "suplyTyNm") or "청년안심" in g(x, "pblancNm") or "공공지원" in g(x, "suplyTyNm")]
    print(f"myhome: 전국 공공지원민간임대·청년안심주택 관련 {len(npriv)}건", [g(x, "pblancNm")[:40] for x in npriv[:5]])
    c3, j3, e3 = call("ltRsdtRcritNtcList", brtcCode="11", numOfRows="10", pageNo="1")
    it3, t3 = items_of(j3) if j3 else ([], None)
    print(f"myhome: 공공분양 조회 HTTP {c3} {e3 or 'OK'} · 서울 totalCount={t3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
