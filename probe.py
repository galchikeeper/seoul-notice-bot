"""읽기 전용 점검: 감시 후보 사이트가 이 실행 환경(GitHub Actions)에서 읽히는지 찍는다.
발송·상태 파일·비밀값을 쓰지 않는다. 실행: python probe.py [--chromium]"""
from __future__ import annotations
import re, sys, asyncio
import requests
from bs4 import BeautifulSoup
import sources as S
import pdfparse as P

UA = {"User-Agent": "Mozilla/5.0 (compatible; seoul-notice-bot/1.0)"}
OUT: list[tuple] = []


def rec(site, method, ok, detail):
    OUT.append((site, method, "읽힘" if ok else "막힘/실패", detail))
    print(f"PROBE | {site} | {method} | {'OK' if ok else 'FAIL'} | {detail}", flush=True)


def tryit(site, method, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {str(e)[:80]}"
    rec(site, method, ok, detail)


def soco_api():
    rows = S.fetch_soco(0, max_pages=1)
    with_file = sum(1 for r in rows if r.get("pdf"))
    return bool(rows), f"공고 {len(rows)}건 · 첨부ID {with_file}건 · 최신 {rows[0]['board_id'] if rows else '-'}"


def soco_view():
    bid = S.fetch_soco(0, max_pages=1)[0]["board_id"]
    r = requests.get(S.SOCO_VIEW.format(bid=bid), headers=UA, timeout=30)
    n = len(re.findall(r"fileDown\.do\?atchFileId=", r.text))
    return r.status_code == 200 and n > 0, f"HTTP {r.status_code} · 페이지 안 첨부 링크 {n}개 (공고 {bid})"


def soco_pdf():
    n = S.fetch_soco(0, max_pages=1)[0]
    r = requests.get(n["pdf"], headers=UA, timeout=60)
    rows, why = P.parse_pdf_bytes(r.content)
    return bool(rows), f"HTTP {r.status_code} · {len(r.content)}B · 표에서 타입 {len(rows)}개" + (f" · {why[:60]}" if why else "")


def hill():
    h = S.fetch_hillstate()
    return bool(h["rows"]), f"글 줄 {len(h['rows'])}개 · 전체 {h['count']}"


def space(sl):
    def f():
        r = requests.get(f"https://www.spacereits.co.kr/{sl}/notice", headers=UA, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = [tr for tr in soup.select("tr") if re.search(r"\d{2}\.\d{2}\.\d{2}", tr.get_text())]
        return r.status_code == 200 and bool(rows), f"HTTP {r.status_code} · {len(r.content)}B · 날짜 줄 {len(rows)}"
    return f


def elyes(kind):
    def f():
        r = requests.post(f"https://www.elyes.co.kr/post/data/{kind}/list",
                          data="searchSelect=title&searchKeyword=&i_iNowPageNo=1&i_iPageSize=10",
                          headers={**UA, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}, timeout=30)
        L = r.json().get(f"{kind}List", [])
        return bool(L), f"HTTP {r.status_code} · 글 {len(L)}건 · 최신: {(L[0].get('nt_title') or '')[:30] if L else '-'}"
    return f


MYHOME_LIST = "https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcList.do"
MYHOME_FORM = {"pageIndex": "1", "srchbrtcCode": "11"}


def myhome_plain():
    r = requests.post(MYHOME_LIST, data=MYHOME_FORM, headers={**UA, "X-Requested-With": "XMLHttpRequest"}, timeout=30)
    try:
        return True, f"HTTP {r.status_code} · 결과 {r.json().get('resultCnt')}건"
    except ValueError:
        return False, f"HTTP {r.status_code} · JSON 아님({len(r.content)}B, 대기열 차단 추정)"


def sh():
    r = requests.get("https://www.i-sh.co.kr/main/lay2/program/S1T294C297/www/brd/m_247/list.do", headers=UA, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    rows = [tr.get_text(" ", strip=True) for tr in soup.select("tbody tr")]
    return r.status_code == 200 and bool(rows), f"HTTP {r.status_code} · 글 줄 {len(rows)} · 첫 줄: {rows[0][:30] if rows else '-'}"


def housing():
    r = requests.get("https://housing.seoul.go.kr", headers=UA, timeout=30)
    t = re.search(r"<title>([^<]*)", r.text)
    return r.status_code == 200, f"HTTP {r.status_code} · 제목 {t.group(1).strip() if t else '-'}"


async def chromium_checks():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(user_agent=UA["User-Agent"])
        # 마이홈: 일반 요청은 대기열(NetFunnel)에 막혀 903. 화면을 연 뒤 사이트의 검색 함수로 서울(11)을 부른다
        try:
            got = []
            async def on(r):
                if "selectRsdtRcritNtcList" in r.url and "srchbrtcCode=11" in (r.request.post_data or ""):
                    try:
                        got.append(await r.json())
                    except Exception:
                        got.append(None)
            pg.on("response", on)
            await pg.goto("https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcView.do",
                          wait_until="networkidle", timeout=60000)
            await pg.evaluate("document.getElementById('srchbrtcCode').value='11'; fnSearch(1);")
            await pg.wait_for_timeout(6000)
            j = next((x for x in reversed(got) if x), None)
            L = (j or {}).get("resultList", [])
            rec("마이홈포털(서울)", "크로미움", bool(L), f"서울 결과 {(j or {}).get('resultCnt')}건 · 최신: {(L[0]['pblancNm'] if L else '-')[:30]}")
        except Exception as e:
            rec("마이홈포털(서울)", "크로미움", False, f"{type(e).__name__}: {str(e)[:80]}")
        for sl in S.SPACEREITS:
            try:
                await pg.goto(f"https://www.spacereits.co.kr/{sl}/notice", wait_until="networkidle", timeout=45000)
                t = await pg.inner_text("body")
                n = len(re.findall(r"\d{2}\.\d{2}\.\d{2}", t))
                rec(f"공간지원리츠 {sl}", "크로미움", n > 0, f"날짜 {n}개 · 본문 {len(t)}자")
            except Exception as e:
                rec(f"공간지원리츠 {sl}", "크로미움", False, f"{type(e).__name__}: {str(e)[:80]}")
        await b.close()


def main():
    tryit("청년안심주택 목록 API", "일반 읽기", soco_api)
    tryit("청년안심주택 공고 페이지 첨부", "일반 읽기", soco_view)
    tryit("청년안심주택 첨부 PDF", "일반 읽기", soco_pdf)
    tryit("힐스테이트 관악 뉴포레", "일반 읽기", hill)
    for sl in S.SPACEREITS:
        tryit(f"공간지원리츠 {sl}", "일반 읽기", space(sl))
    tryit("롯데 엘리스 모집공고", "일반 읽기(내부 목록)", elyes("recruit"))
    tryit("롯데 엘리스 공지", "일반 읽기(내부 목록)", elyes("notice"))
    tryit("마이홈포털(서울)", "일반 읽기", myhome_plain)
    tryit("SH 주택임대 공고", "일반 읽기", sh)
    tryit("서울주거포털", "일반 읽기", housing)
    if "--chromium" in sys.argv:
        asyncio.run(chromium_checks())
    print("\n| 사이트 | 방법 | 결과 | 내용 |\n|---|---|---|---|")
    for row in OUT:
        print("| " + " | ".join(row) + " |")


if __name__ == "__main__":
    main()
