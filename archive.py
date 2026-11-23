"""받은 공고문 파일의 사본 보관 — 원래 사이트 링크가 막혀도 카드에서 항상 열리게.

1순위(설정돼 있으면): 슬랙 봇 토큰(SLACK_BOT_TOKEN, files:write·chat:write) + SLACK_CHANNEL_ID → 카드 스레드에 파일 첨부(slack.py)
기본: 이 저장소(공개)의 GitHub 릴리스 'notice-files' 에 자산으로 올리고 그 내려받기 주소를 카드 버튼에 단다.
      저장소 git 기록에는 들어가지 않는다(복제 크기 그대로). 같은 이름이 이미 있으면 그 주소를 쓴다.
토큰: Actions 의 GITHUB_TOKEN(워크플로 permissions: contents: write) 또는 로컬 GH_TOKEN.
"""
from __future__ import annotations
import datetime as dt, os, re
import requests

TAG = "notice-files"
API = "https://api.github.com"
MIME = {"pdf": "application/pdf", "hwp": "application/x-hwp", "hwpx": "application/haansofthwpx",
        "jpg": "image/jpeg", "png": "image/png", "gif": "image/gif",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def _repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or os.environ.get("NOTICE_REPO", "galchikeeper/seoul-notice-bot")


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}


_REL: dict = {}


def _release(tok: str) -> dict:
    if _REL:
        return _REL
    r = requests.get(f"{API}/repos/{_repo()}/releases/tags/{TAG}", headers=_h(tok), timeout=30)
    if r.status_code == 404:
        r = requests.post(f"{API}/repos/{_repo()}/releases", headers=_h(tok), timeout=30, json={
            "tag_name": TAG, "name": "공고문 사본(봇 자동 보관)", "prerelease": True,
            "body": "임대주택 봇이 받은 공고문 원본 사본. 원래 사이트 링크가 막혀도 카드의 '공고문 사본' 버튼으로 연다."})
    r.raise_for_status()
    _REL.update(r.json())
    return _REL


def asset_name(n: dict, i: int, kind: str) -> str:
    """영문·숫자만(깃허브가 한글 이름을 바꿔 버려서). 예: 20261123-sh-313647-1.pdf"""
    src = n.get("source") or "soco"
    nid = re.sub(r"[^0-9A-Za-z]", "", str(n.get("board_id") or "").split(":")[-1])[:24] or "x"
    return f"{dt.date.today():%Y%m%d}-{src}-{nid}-{i}.{kind or 'bin'}"


def to_github(n: dict, files: list[dict]) -> list[dict]:
    """[{name(원래 이름), url(사본 주소), kind}] — 공고문 앞쪽 최대 3개."""
    tok = _token()
    if not tok:
        print("archive: GitHub 토큰 없음 — 사본 보관 건너뜀")
        return []
    rel = _release(tok)
    have = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    out = []
    for i, f in enumerate([f for f in files if f.get("data") and f.get("kind") in MIME][:3], 1):
        nm = asset_name(n, i, f["kind"])
        if nm not in have:
            up = rel["upload_url"].split("{")[0]
            r = requests.post(up, params={"name": nm}, data=f["data"], timeout=120,
                              headers={**_h(tok), "Content-Type": MIME[f["kind"]]})
            if r.status_code == 422:                 # 같은 이름이 방금 올라감
                rel.clear()
                have = {a["name"]: a["browser_download_url"] for a in _release(tok).get("assets", [])}
            else:
                r.raise_for_status()
                have[nm] = r.json()["browser_download_url"]
        if nm in have:
            out.append({"name": f["name"], "url": have[nm], "kind": f["kind"], "data": f["data"]})
    return out
