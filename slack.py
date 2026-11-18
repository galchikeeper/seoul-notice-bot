"""슬랙 Incoming Webhook 발송 — 공고 1건 = Block Kit 카드 1개.

웹훅 주소는 비밀값이다. 저장소가 공개이고 Actions 로그도 공개일 수 있으므로
주소가 담길 수 있는 예외 메시지는 절대 출력하지 않는다(상태 코드·예외 종류만).
"""
from __future__ import annotations
import os, re, requests

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

    @classmethod
    def from_env(cls) -> "Slack | None":
        url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
        return cls(url) if url else None

    def _post(self, payload: dict) -> bool:
        payload = {**payload, "unfurl_links": False, "unfurl_media": False}   # 미리보기 상자 끔
        try:
            r = requests.post(self.webhook, json=payload, timeout=30)
        except Exception as e:                      # 메시지에 주소가 섞일 수 있어 종류만
            print(f"slack: 전송 오류 ({type(e).__name__})")
            return False
        if r.status_code == 200:
            return True
        # 본문은 슬랙 오류 코드(예: invalid_blocks) 형태일 때만 찍는다 — 다른 내용이 반사될 수 있으므로
        code = r.text.strip() if re.fullmatch(r"[a-z_]{1,40}", r.text.strip()) else "(본문 생략)"
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
