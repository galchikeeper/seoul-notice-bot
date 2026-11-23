"""슬랙 Incoming Webhook 발송 — 공고 1건 = Block Kit 카드 1개.

웹훅 주소는 비밀값이다. 저장소가 공개이고 Actions 로그도 공개일 수 있으므로
주소가 담길 수 있는 예외 메시지는 절대 출력하지 않는다(상태 코드·예외 종류만).
"""
from __future__ import annotations
import os, re, time, requests

CARDS_PER_MESSAGE = 12          # 카드 1개 = 블록 4개, 슬랙 메시지당 블록 50개 제한


def _esc(s: str) -> str:
    """슬랙 mrkdwn 특수문자 처리."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def card_blocks(c: dict, idx: int) -> list[dict]:
    """c: title, summary, meta, notice_url, apply_url(없으면 빈 문자열)."""
    buttons = [{"type": "button", "action_id": f"notice_{idx}",
                "text": {"type": "plain_text", "text": "공고 보기"},
                "url": c["notice_url"][:3000]}]
    if c.get("apply_url") and len(c["apply_url"]) <= 3000:
        buttons.append({"type": "button", "action_id": f"apply_{idx}", "style": "primary",
                        "text": {"type": "plain_text", "text": "신청하기"},
                        "url": c["apply_url"]})
    for k, f in enumerate((c.get("files") or [])[:2]):          # 받은 공고문 사본(원래 링크가 막혀도 열림)
        label = "📎 공고문 사본" if k == 0 else f"📎 첨부 {k + 1}"
        buttons.append({"type": "button", "action_id": f"file_{idx}_{k}",
                        "text": {"type": "plain_text", "text": f"{label}({f.get('kind', '').upper()})"[:75]},
                        "url": f["url"][:3000]})
    return [
        {"type": "section",
         "text": {"type": "mrkdwn", "text": f"*{_esc(c['title'])}*\n{_esc(c['summary'])}"[:2900]}},
        {"type": "context",
         "elements": [{"type": "mrkdwn", "text": _esc(c["meta"])[:2900]}]},
        {"type": "actions", "elements": buttons},
        {"type": "divider"},
    ]


class Slack:
    def __init__(self, webhook: str):
        self.webhook = (webhook or "").strip()
        self.last_error = ""
        self.table_mode = ""            # 처음 성공한 표 형식을 기억한다: data_table / table / text
        self.elig_mode = ""             # 자격 형식: table(2열 표) / box(네모 칸)

    @classmethod
    def from_env(cls) -> "Slack | None":
        url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
        return cls(url) if url else None

    def _post(self, payload: dict) -> bool:
        payload = {**payload, "unfurl_links": False, "unfurl_media": False}   # 미리보기 상자 끔
        time.sleep(1.1)                             # 웹훅은 초당 1건 정도로 제한된다
        for attempt in range(3):
            try:
                r = requests.post(self.webhook, json=payload, timeout=30)
            except Exception as e:                  # 메시지에 주소가 섞일 수 있어 종류만
                # 응답만 늦은 것일 수 있어(이미 게시됐을 수 있음) 같은 실행에서 다시 보내지 않는다
                self.last_error = f"exception {type(e).__name__}"
                print(f"slack: 전송 오류 ({type(e).__name__})")
                return False
            if r.status_code == 429 and attempt < 2:  # 전송 제한 — 기다렸다 같은 내용으로 다시
                try:
                    wait = min(int(r.headers.get("Retry-After", "5")), 60)
                except ValueError:
                    wait = 5
                print(f"slack: 전송 제한(429) — {wait}초 뒤 다시 보냄")
                time.sleep(wait)
                continue
            break
        if r.status_code == 200:
            self.last_error = ""
            return True
        # 본문은 슬랙 오류 코드(예: invalid_blocks) 형태일 때만 찍는다 — 다른 내용이 반사될 수 있으므로
        code = r.text.strip() if re.fullmatch(r"[a-z_]{1,40}", r.text.strip()) else "(본문 생략)"
        self.last_error = f"{r.status_code} {code}"
        print(f"slack: 실패 {r.status_code} {code}")
        return False

    def send_text(self, text: str) -> bool:
        return self._post({"text": text})

    def send_notices(self, cards: list[dict], header: str = "", footer: str = "") -> tuple[int, int]:
        """카드들을 메시지 여러 개로 나눠 보낸다. (보낸 메시지 수, 실패한 메시지 수)."""
        if not cards:
            ok = self.send_text("\n".join(x for x in [header or "확인 완료", footer] if x))
            return (1, 0) if ok else (0, 1)
        sent = failed = 0
        chunks = [cards[i:i + CARDS_PER_MESSAGE] for i in range(0, len(cards), CARDS_PER_MESSAGE)]
        for ci, chunk in enumerate(chunks):
            blocks: list[dict] = []
            if header and ci == 0:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{_esc(header)}*"}})
            for k, c in enumerate(chunk):
                blocks += card_blocks(c, ci * CARDS_PER_MESSAGE + k)
            if footer and ci == len(chunks) - 1:
                blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _esc(footer)[:2900]}]})
            fallback = header if (header and ci == 0) else chunk[0]["title"]
            if self._post({"text": fallback, "blocks": blocks}):
                sent += 1
            else:
                failed += 1
        return sent, failed


# ─────────────────────────── 공고 1건 = 메시지 1개 (표) ───────────────────────────
# c (bot.slack_item 이 만든 값):
#   top: 맨 위 한 줄, flags: 그 위에 붙일 표시 목록(예: "📐 면적 확인 필요")
#   rows: [[타입, 전용㎡, 대상, 비율, 보증금, 월세], ...] (문자열) — None 이면 표 없음(PDF 없는 출처)
#   note: 표 아래 단위 줄, fallback_text: 표를 못 보낼 때 쓸 줄 목록
#   notice_url, apply_url
HEAD_FULL = ["타입", "전용㎡", "대상", "보증금 비율", "보증금(만원)", "월세(만원)"]
HEAD_COMPACT = ["타입(대상)", "전용㎡", "보증금 비율", "보증금(만원)", "월세(만원)"]
TABLE_COLS = os.environ.get("SLACK_TABLE_COLS", "full")      # full | compact(휴대폰에서 잘리면)


def _rows_for(c: dict) -> tuple[list[str], list[list[str]]]:
    if c.get("head"):                       # 출처마다 다른 열(예: 엘리스 — 타입·보증금·월세·층·향·용도)
        return c["head"], c["rows"]
    if TABLE_COLS == "compact":
        return HEAD_COMPACT, [[f"{r[0]}({r[2]})" if r[2] else r[0], r[1], r[3], r[4], r[5]] for r in c["rows"]]
    # 공고문 글자 줄에서 읽은 표(예: 옥류서원)는 대상·보증금 비율 정보가 없다 — 전부 비었거나 '-' 인 열은 빼서
    # '확인 실패' 칸이 깨진 것처럼 보이지 않게 한다(값이 하나라도 있으면 열을 남김)
    keep = [i for i in range(len(HEAD_FULL)) if i not in (2, 3)
            or any((r[i] or "").strip() not in ("", "-") for r in c["rows"])]
    return [HEAD_FULL[i] for i in keep], [[r[i] for i in keep] for r in c["rows"]]


def _num_cell(t: str) -> dict:
    v = t.replace(",", "")
    try:
        return {"type": "raw_number", "value": float(v), "text": t}
    except ValueError:
        return {"type": "raw_text", "text": t or "확인 실패"}


def table_block(c: dict, mode: str) -> dict:
    head, rows = _rows_for(c)
    money = set(c["money_cols"]) if c.get("head") and c.get("money_cols") else {len(head) - 2, len(head) - 1}
    if mode == "data_table":
        cells = [[{"type": "raw_text", "text": h} for h in head]]
        cells += [[_num_cell(x) if i in money else {"type": "raw_text", "text": x or "확인 실패"}
                   for i, x in enumerate(r)] for r in rows]
        return {"type": "data_table", "caption": c["caption"][:200], "rows": cells,
                "page_size": max(1, min(len(rows), 30))}
    cells = [[{"type": "raw_text", "text": h} for h in head]]
    cells += [[{"type": "raw_text", "text": x or "확인 실패"} for x in r] for r in rows]
    return {"type": "table", "rows": cells,
            "column_settings": [{"is_wrapped": True}] + [{"align": "right"} if i in money else None
                                                          for i in range(1, len(head))]}


ELIG_SPLIT = os.environ.get("ELIG_SPLIT", "0") == "1"   # 1이면 '소득·자산·차량' 행을 '소득' / '자산·차량' 두 행으로


def _elig_rows(c: dict) -> list[tuple[str, str]]:
    import eligibility as E
    rows = E.table_rows(c.get("elig_items") or [])
    return E.split_money(rows) if ELIG_SPLIT else rows


def elig_lines(c: dict) -> str:
    rows = _elig_rows(c)
    return "\n".join(_esc(f"{k}  {v}" if k != "※" else f"※ {v}") for k, v in rows) if rows else "자격  공고문 확인"


def elig_box(c: dict) -> str:
    """예비 형식: 자격 네모 칸(코드 블록). 슬랙이 표 두 개를 거절할 때만 쓴다."""
    if not c.get("elig_items"):
        return "*👤 자격*  공고문 확인"
    return f"*👤 자격*\n```{elig_lines(c)}```"


def elig_table(c: dict) -> dict:
    """자격 2열 표(자격 | 내용). 내용 열은 줄바꿈(is_wrapped) — 칸 안 글이 '…'로 잘리지 않게."""
    cells = [[{"type": "raw_text", "text": h} for h in ("자격", "내용")]]
    cells += [[{"type": "raw_text", "text": k}, {"type": "raw_text", "text": v}] for k, v in _elig_rows(c)]
    return {"type": "table", "rows": cells, "column_settings": [{"is_wrapped": True}, {"is_wrapped": True}]}


def notice_blocks(c: dict, mode: str, elig_style: str = "box") -> list[dict]:
    """mode: 임대조건 표 형식(data_table/table/text). elig_style: 자격을 표(table)로 붙일지 네모 칸(box)으로 붙일지.
    임대조건 표와 자격 표 사이에는 다른 블록을 넣지 않는다."""
    top = "\n".join([*c.get("flags", []), f"*{_esc(c['top'])}*"])
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": top[:2900]}}]
    if c.get("gate"):                                       # 보내기 전 관문 실패 — 무엇이 왜 (깨진 카드를 표시 없이 보내지 않는다)
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": (
            "⚠️ *자동 점검 실패* — 아래 항목은 공고문으로 직접 확인\n" + "\n".join("• " + _esc(x) for x in c["gate"]))[:2900]}]})
    has_elig = "elig_items" in c or bool(c.get("elig"))
    if c.get("rows") and mode in ("data_table", "table"):
        blocks.append(table_block(c, mode))
    else:
        rent = "\n".join(_esc(x) for x in c["fallback_text"].split("\n"))
        if has_elig and elig_style == "box" and c.get("elig_items"):     # 표가 없으면 임대조건 줄과 자격을 한 칸에
            rent = f"*🏠 임대조건 · 👤 자격*\n```{rent}\n\n{elig_lines(c)}```"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": rent[:2900]}})
    if has_elig and not c.get("elig_items"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*👤 자격*  공고문 확인"}})
    elif has_elig and elig_style == "table":              # 임대조건 표 바로 아래에 자격 표
        blocks.append(elig_table(c))
    elif has_elig and c.get("rows") and mode in ("data_table", "table"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": elig_box(c)[:2900]}})
    note = c["note"] + (" · ✅ 자동 점검 통과(링크·조건·공고문 파일)" if "gate" in c and not c["gate"] else "")
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _esc(note)[:2900]}]})
    blocks.append(card_blocks({**c, "title": "", "summary": "", "meta": ""}, 0)[2])   # [공고 보기] [신청하기]
    return blocks


def _send_notice(self, c: dict) -> bool:
    """임대조건 표 data_table → table → 줄 목록, 자격은 표 → 네모 칸 순서로 시도한다.
    한 메시지에 표 두 개를 슬랙이 거절하면(400 invalid_…) 자격만 네모 칸으로 바꿔 다시 보낸다. 처음 성공한 조합을 기억한다."""
    if c.get("rows"):
        order = [(m, e) for m in ("data_table", "table", "text") for e in ("table", "box")]
        if self.table_mode:
            order = [x for x in order if ("data_table", "table", "text").index(x[0])
                     >= ("data_table", "table", "text").index(self.table_mode)]
    else:
        order = [("text", "table"), ("text", "box")]
    if self.elig_mode == "box" or not c.get("elig_items"):     # 자격 항목이 없으면 표/칸 구분이 의미 없다
        order = [x for x in order if x[1] == "box"]
    for mode, est in order:
        if self._post({"text": c["top"], "blocks": notice_blocks(c, mode, est)}):
            if c.get("rows"):
                if self.table_mode != mode:
                    print(f"slack: 표 형식 {mode} 로 보냄")
                self.table_mode = mode
            if c.get("elig_items"):
                em = "box" if est == "box" else "table"
                if self.elig_mode != em:
                    print(f"slack: 자격 형식 {em} 로 보냄")
                self.elig_mode = em
            return True
        # 형식을 바꿔 다시 보내는 건 블록 검증 오류(400 invalid_…)일 때만. 그 밖(429·5xx·연결 오류)은 여기서 멈춘다
        if (mode, est) == order[-1] or not re.match(r"400 invalid_", self.last_error):
            print(f"slack: 공고 전송 실패({self.last_error}) — 형식을 바꾸지 않고 멈춤")
            return False
        print(f"slack: {mode}/{est} 블록 거부({self.last_error}) → 다음 형식으로 다시 보냄")
    return False


Slack.send_notice = _send_notice


# ─────────────────────────── 봇 토큰 모드(선택) — 카드 스레드에 공고문 파일 첨부 ───────────────────────────
# 웹훅은 파일을 올릴 수 없고 메시지 ts 도 돌려주지 않는다. SLACK_BOT_TOKEN(chat:write, files:write)과
# SLACK_CHANNEL_ID 가 둘 다 있으면 chat.postMessage 로 보내고, 그 스레드에 받은 공고문 파일을 올린다.
# 둘 중 하나라도 없으면 지금처럼 웹훅 + '📎 공고문 사본' 버튼(GitHub 릴리스 사본)만 쓴다.
class SlackBot(Slack):
    API = "https://slack.com/api/"

    def __init__(self, token: str, channel: str):
        super().__init__("bot")
        self.token, self.channel, self.last_ts = token, channel, ""

    @classmethod
    def from_env(cls) -> "SlackBot | None":
        t, ch = os.environ.get("SLACK_BOT_TOKEN", "").strip(), os.environ.get("SLACK_CHANNEL_ID", "").strip()
        return cls(t, ch) if t and ch else None

    def _api(self, method: str, **kw):
        h = {"Authorization": f"Bearer {self.token}"}
        r = requests.post(self.API + method, headers=h, timeout=60, **kw)
        try:
            return r.json()
        except ValueError:
            return {"ok": False, "error": f"http_{r.status_code}"}

    def _post(self, payload: dict) -> bool:
        payload = {**payload, "channel": self.channel, "unfurl_links": False, "unfurl_media": False}
        time.sleep(1.1)
        j = self._api("chat.postMessage", json=payload)
        if j.get("ok"):
            self.last_error, self.last_ts = "", j.get("ts", "")
            return True
        err = str(j.get("error", "unknown"))[:40]
        self.last_error = f"400 {err}" if err.startswith("invalid_") else f"api {err}"
        print(f"slack(bot): 실패 {self.last_error}")
        return False

    def upload_thread(self, files: list[dict]) -> int:
        """마지막으로 보낸 카드의 스레드에 파일 올리기(files.getUploadURLExternal → 업로드 → completeUploadExternal)."""
        done = 0
        for f in files:
            data = f.get("data") or b""
            if not data or not self.last_ts:
                continue
            fn = re.sub(r"[\\/:*?\"<>|]", "_", f.get("name") or "공고문")[:80]
            j = self._api("files.getUploadURLExternal", data={"filename": fn, "length": len(data)})
            if not j.get("ok"):
                print(f"slack(bot): 업로드 주소 실패 {j.get('error')}")
                continue
            requests.post(j["upload_url"], files={"file": (fn, data)}, timeout=120)
            k = self._api("files.completeUploadExternal", json={
                "files": [{"id": j["file_id"], "title": fn}], "channel_id": self.channel, "thread_ts": self.last_ts})
            done += bool(k.get("ok"))
            if not k.get("ok"):
                print(f"slack(bot): 스레드 첨부 실패 {k.get('error')}")
        return done


def from_env() -> "Slack | None":
    """봇 토큰 모드가 설정돼 있으면 그쪽, 아니면 웹훅."""
    return SlackBot.from_env() or Slack.from_env()
