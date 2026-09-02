"""카카오톡 '나에게 보내기' — 피드 템플릿(이미지 포함) 발송."""
from __future__ import annotations
import json, os, requests

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


class Kakao:
    def __init__(self, rest_key: str, refresh_token: str, client_secret: str = ""):
        self.rest_key = rest_key
        self.refresh_token = refresh_token
        self.client_secret = client_secret   # 콘솔에서 '클라이언트 시크릿'을 켠 경우 필요
        self.access_token = ""
        self.new_refresh_token = ""

    def refresh(self) -> None:
        """리프레시 토큰으로 액세스 토큰 재발급.

        카카오는 리프레시 토큰의 잔여 유효기간이 1개월 미만일 때만 새 리프레시
        토큰을 함께 내려준다. 새로 받으면 self.new_refresh_token 에 담아두고,
        호출부에서 GitHub Secret 을 갱신하도록 한다.
        """
        data = {
            "grant_type": "refresh_token",
            "client_id": self.rest_key,
            "refresh_token": self.refresh_token,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        r = requests.post(TOKEN_URL, timeout=30, data=data)
        r.raise_for_status()
        d = r.json()
        self.access_token = d["access_token"]
        if d.get("refresh_token"):
            self.new_refresh_token = d["refresh_token"]

    # ── 발송 ─────────────────────────────────────────────
    def _send(self, template: dict) -> None:
        r = requests.post(
            SEND_URL, timeout=30,
            headers={"Authorization": f"Bearer {self.access_token}"},
            data={"template_object": json.dumps(template, ensure_ascii=False)},
        )
        if r.status_code != 200:
            raise RuntimeError(f"kakao send failed {r.status_code}: {r.text[:300]}")

    def send_text(self, text: str, link: str = "https://soco.seoul.go.kr") -> None:
        self._send({
            "object_type": "text",
            "text": text[:200],
            "link": {"web_url": link, "mobile_web_url": link},
        })

    def send_feed(self, *, title: str, desc: str, image_url: str,
                  link: str, button: str = "공고 보기",
                  image_w: int = 900, image_h: int = 1400,
                  items: list[tuple[str, str]] | None = None) -> None:
        """이미지가 말풍선 안에 박히는 카드형 메시지."""
        tpl = {
            "object_type": "feed",
            "content": {
                "title": title[:60],
                "description": desc[:200],
                "image_url": image_url,
                "image_width": image_w,
                "image_height": image_h,
                "link": {"web_url": link, "mobile_web_url": link},
            },
            "buttons": [{
                "title": button[:14],
                "link": {"web_url": link, "mobile_web_url": link},
            }],
        }
        if items:
            tpl["item_content"] = {
                "items": [{"item": k[:6], "item_op": v[:20]} for k, v in items[:5]]
            }
        self._send(tpl)


def update_github_secret(new_refresh: str) -> bool:
    """새 리프레시 토큰을 GitHub Secret 에 자동 반영 (GH_PAT 가 있을 때만).

    없으면 False 를 돌려주고, 호출부가 카톡으로 수동 갱신을 안내한다.
    """
    pat, repo = os.environ.get("GH_PAT"), os.environ.get("GITHUB_REPOSITORY")
    if not (pat and repo):
        return False
    try:
        from nacl import encoding, public
        h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
        k = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
                         headers=h, timeout=30).json()
        box = public.SealedBox(public.PublicKey(k["key"].encode(), encoding.Base64Encoder()))
        import base64
        enc = base64.b64encode(box.encrypt(new_refresh.encode())).decode()
        r = requests.put(
            f"https://api.github.com/repos/{repo}/actions/secrets/KAKAO_REFRESH_TOKEN",
            headers=h, timeout=30,
            json={"encrypted_value": enc, "key_id": k["key_id"]})
        return r.status_code in (201, 204)
    except Exception:
        return False
