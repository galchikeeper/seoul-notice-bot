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
    return HEAD_FULL, c["rows"]


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


def notice_blocks(c: dict, mode: str) -> list[dict]:
    top = "\n".join([*c.get("flags", []), f"*{_esc(c['top'])}*"])
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": top[:2900]}}]
    if c.get("rows") and mode in ("data_table", "table"):
        blocks.append(table_block(c, mode))
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _esc(c["fallback_text"])[:2900]}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _esc(c["note"])[:2900]}]})
    blocks.append(card_blocks({**c, "title": "", "summary": "", "meta": ""}, 0)[2])   # [공고 보기] [신청하기]
    return blocks


def _send_notice(self, c: dict) -> bool:
    """표 형식을 data_table → table → 줄 목록 순서로 시도한다. 어느 형식으로 나갔는지 로그에 남긴다."""
    order = ["data_table", "table", "text"]
    if self.table_mode:
        order = order[order.index(self.table_mode):]
    if not c.get("rows"):
        order = ["text"]
    for mode in order:
        if self._post({"text": c["top"], "blocks": notice_blocks(c, mode)}):
            if c.get("rows"):
                if self.table_mode != mode:
                    print(f"slack: 표 형식 {mode} 로 보냄")
                self.table_mode = mode
            return True
        # 형식을 바꿔 다시 보내는 건 블록 검증 오류(400 invalid_…)일 때만. 그 밖(429·5xx·연결 오류)은 여기서 멈춘다
        if mode == "text" or not re.match(r"400 invalid_", self.last_error):
            print(f"slack: 공고 전송 실패({self.last_error}) — 형식을 바꾸지 않고 멈춤")
            return False
        print(f"slack: {mode} 블록 거부({self.last_error}) → 다음 형식으로 다시 보냄")
    return False


Slack.send_notice = _send_notice
