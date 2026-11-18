"""슬랙 Incoming Webhook 발송.

웹훅 주소는 비밀값이다. 저장소가 공개이고 Actions 로그도 공개일 수 있으므로
주소가 담길 수 있는 예외 메시지는 절대 출력하지 않는다(상태 코드·예외 종류만).
"""
from __future__ import annotations
import os, re, requests


class Slack:
    def __init__(self, webhook: str):
        self.webhook = (webhook or "").strip()

    @classmethod
    def from_env(cls) -> "Slack | None":
        url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
        return cls(url) if url else None

    def _post(self, payload: dict) -> bool:
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

    def send_card(self, *, title: str, lines: list[str], image_url: str = "") -> bool:
        """카톡 카드와 같은 내용. 이미지가 아직 CDN 에 없으면 슬랙이 거부하므로
        그때는 이미지 주소를 글로 붙여 다시 보낸다."""
        text = "\n".join([f"*{title}*", *[l for l in lines if l]])
        if image_url:
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}},
                {"type": "image", "image_url": image_url, "alt_text": title[:100]},
            ]
            if self._post({"text": text, "blocks": blocks}):
                return True
            text += f"\n카드 이미지 {image_url}"
        return self.send_text(text)
