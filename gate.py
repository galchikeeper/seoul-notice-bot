"""보내기 전 관문 — 카드마다 점검표를 돌리고, 실패하면 재시도 → 다른 방법 → 그래도 안 되면 카드에 이유를 적어 보낸다.

점검표(하나라도 실패면 '실패'):
  ① 링크: 공고 보기 주소가 200 으로 열리고 오류·로그인 화면이 아니며 제목 글자가 보인다
  ② 조건: 임대조건(표/줄) · 자격 · 마감(신청 일정)이 채워졌다
  ③ 파일: 공고문 파일을 받아 사본 링크(또는 스레드 첨부)를 만들었다

순서(prepare):
  1회차 읽기 → 점검 → 실패 중 '네트워크성'(링크·파일 받기)이 있으면 간격 두고 최대 3회 다시 읽음
  → 그래도 남은 실패는 reader 가 이미 시도한 '다른 방법'(그림 OCR·페이지 본문) 결과로 판정
  → 남은 실패는 card['gate'] 에 '무엇이 왜' 한 줄씩, failures.jsonl 에 기록. 카드는 표시를 달고 보낸다.
"""
from __future__ import annotations
import datetime as dt, json, os, time
from pathlib import Path

import reader as R

ROOT = Path(__file__).resolve().parent
FAILURES = ROOT / "failures.jsonl"
KST = dt.timezone(dt.timedelta(hours=9))


def check(card: dict, res: dict, archived: list[dict]) -> list[str]:
    """실패 항목 목록(문구: '무엇 — 왜'). 비면 통과."""
    out = []
    if res.get("page_problem"):
        out.append(f"링크 — {res['page_problem']}")
    elif card.get("notice_url") and card["notice_url"] != res.get("page_url"):
        try:
            r = R.get(card["notice_url"], tries=1, timeout=30)
            p = R.page_problem(r, card.get("top", ""))
        except Exception as e:
            p = f"열리지 않음({type(e).__name__})"
        if p:
            out.append(f"링크 — {p}")
    files = res.get("files") or []
    got = [f for f in files if f.get("data") and f.get("kind") not in ("html", "?")]
    if not files:
        out.append("파일 — 공고 페이지에서 첨부를 찾지 못함")
    elif not got:
        out.append("파일 — " + "; ".join(f"{f['name'][:20]}: {f['how']}" for f in files[:2]))
    elif not archived:
        out.append("파일 — 받았지만 사본 보관(업로드) 실패")
    if card.get("skip"):
        pass                                   # 전용 30㎡ 미만 타입만 있는 공고 — 원래 보내지 않는 카드(임대조건은 읽었음)
    elif not card.get("rows"):
        why = _why_text(res)
        out.append(f"임대조건 — 공고문에서 금액 표·줄을 찾지 못함{why}")
    if not card.get("elig_items"):
        out.append(f"자격 — 공고문에서 자격 문장을 찾지 못함{_why_text(res)}")
    if "마감 공고문 확인" in card.get("top", "") or "마감 확인 필요" in card.get("top", ""):
        out.append("마감 — 신청·접수 기간 날짜를 찾지 못함")
    return out


def _why_text(res: dict) -> str:
    fs = [f for f in res.get("files") or [] if f.get("how")]
    if not fs:
        return "(읽을 파일 없음)"
    f = fs[0]
    return f"({f['kind'] or '?'} · {f['how']} · 글자 {f['chars']:,}자)"


NET = ("링크", "파일")


def prepare(n: dict, build, archive=None, tries: int = 3) -> tuple[dict, dict, list[str]]:
    """n: 수집한 공고, build(m)->card(slack_item), archive(n, files)->[{name,url}] 또는 None.
    돌려주는 값: (카드, 읽은 결과, 남은 실패)."""
    waits = R._waits()
    res, card, archived, probs = {}, {}, [], ["처음"]
    for i in range(tries):
        res = R.read(n)
        m = R.fill(n, res)
        card = build(m)
        archived = []
        if archive and any(f.get("data") for f in res["files"]):
            try:
                archived = archive(n, res["files"]) or []
            except Exception as e:
                print(f"gate: 사본 보관 실패 ({type(e).__name__})")
        probs = check(card, res, archived)
        net = [p for p in probs if p.startswith(NET) and "사본 보관" not in p]   # 다시 읽어서 나아질 수 있는 실패만
        if not net or i == tries - 1:
            break
        print(f"gate: {n.get('title','')[:30]} — {i + 1}회차 실패({'; '.join(probs)[:120]}) → {waits[min(i, len(waits)-1)]}초 뒤 다시")
        time.sleep(waits[min(i, len(waits) - 1)])
    card["files"] = archived
    card["gate"] = probs
    card["read_how"] = [f"{f['name'][:30]}: {f['how']}" for f in res.get("files", [])] + m.get("_read_how", [])
    return card, res, probs


def record(n: dict, probs: list[str], res: dict, path: Path = FAILURES) -> None:
    """관문 실패 1건을 실패 목록에 쌓는다(해결 에이전트가 읽음). 같은 공고·같은 실패는 하루 한 번만."""
    if not probs:
        return
    today = dt.datetime.now(KST).strftime("%Y-%m-%d")
    key = f"{n.get('board_id') or n.get('url')}|{'|'.join(sorted(probs))}"
    if path.exists():
        for ln in path.read_text().splitlines():
            try:
                x = json.loads(ln)
            except ValueError:
                continue
            if x.get("key") == key and x.get("date") == today:
                return
    row = {"date": today, "key": key, "source": n.get("source") or "soco", "id": str(n.get("board_id") or ""),
           "title": (n.get("raw_title") or n.get("title") or "")[:120], "url": n.get("url", ""),
           "problems": probs, "files": [{k: f.get(k) for k in ("name", "url", "kind", "size", "how", "chars")}
                                         for f in res.get("files", [])],
           "run": os.environ.get("GITHUB_RUN_ID", "local"), "status": "open"}
    with path.open("a") as fp:
        fp.write(json.dumps(row, ensure_ascii=False) + "\n")
