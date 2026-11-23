#!/bin/bash
# 배포 관문 — 고친 코드를 main(실제 발송 코드)에 반영하는 유일한 길.
#   사용: fix/<이름> 가지에서 커밋한 뒤  ./deploy.sh "<한 줄 요약>"
#   1) origin/main 을 가지에 합침 → 2) 가지를 올리고 GitHub Actions '시험 묶음' 워크플로를 그 커밋으로 실행
#   3) 전부 통과면 main 에 반영(빨리감기). 하나라도 실패·시간 초과면 반영하지 않고 슬랙 #알림 에 🔴 보고, 종료 코드 1.
# 강제 푸시는 하지 않는다. 웹훅 주소는 출력하지 않는다.
set -uo pipefail
cd "$(dirname "$0")"
SUMMARY="${1:-임대주택 봇 수정}"
BR=$(git rev-parse --abbrev-ref HEAD)

report() {   # $1 = 한 줄 내용
  local url
  url=$(security find-generic-password -s claude-slack-webhook -w 2>/dev/null || true)
  echo "보고: $1"
  [ -n "$url" ] && python3 - "$url" "$1" <<'PY' >/dev/null 2>&1 || true
import json, sys, urllib.request
req = urllib.request.Request(sys.argv[1], data=json.dumps({"text": sys.argv[2]}).encode(),
                             headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=20)
PY
}

if [[ "$BR" != fix/* ]]; then
  echo "fix/ 가지에서만 배포한다(지금: $BR)"; exit 2
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "커밋 안 한 변경이 있다 — 먼저 커밋"; exit 2
fi

git fetch -q origin main
if ! git merge -q --no-edit origin/main; then
  git merge --abort
  report "🔴 임대주택 봇 배포 중단 — $BR 가 main 과 충돌(자동 병합 실패). 반영 안 함 · $SUMMARY"
  exit 1
fi
git push -q -u origin "$BR" || { report "🔴 임대주택 봇 배포 중단 — 가지 올리기 실패 · $SUMMARY"; exit 1; }
SHA=$(git rev-parse HEAD)

gh workflow run test.yml --ref "$BR" >/dev/null || { report "🔴 임대주택 봇 배포 중단 — 시험 워크플로 실행 실패 · $SUMMARY"; exit 1; }
RUN=""
for _ in $(seq 1 30); do
  sleep 10
  RUN=$(gh run list --workflow test.yml --branch "$BR" --event workflow_dispatch --json databaseId,headSha \
        -q "[.[] | select(.headSha==\"$SHA\")][0].databaseId" 2>/dev/null)
  [ -n "$RUN" ] && break
done
[ -z "$RUN" ] && { report "🔴 임대주택 봇 배포 중단 — 시험 실행을 찾지 못함 · $SUMMARY"; exit 1; }
echo "시험 실행: $RUN"
gh run watch "$RUN" --exit-status --interval 20 >/dev/null 2>&1
CONC=$(gh run view "$RUN" --json conclusion -q .conclusion)
if [ "$CONC" != "success" ]; then
  FAILS=$(gh run view "$RUN" --log-failed 2>/dev/null | grep -oE "FAILED tests/[^ ]+" | sort -u | head -8 | tr '\n' ' ')
  report "🔴 임대주택 봇 배포 안 함 — 시험 묶음 실패($CONC): ${FAILS:-로그 확인} · 실행 https://github.com/galchikeeper/seoul-notice-bot/actions/runs/$RUN · $SUMMARY"
  exit 1
fi

# main 이 그사이 봇 기록 커밋(state.json·failures.jsonl·cards)만 받았으면 합쳐서 바로 반영, 코드가 바뀌었으면 다시 시험
for _ in 1 2 3; do
  if git push -q origin HEAD:main; then
    echo "배포 완료: $SHA → main"
    exit 0
  fi
  git fetch -q origin main
  CHANGED=$(git diff --name-only HEAD...origin/main | grep -vE '^(state\.json|failures\.jsonl|cards/)' || true)
  if [ -n "$CHANGED" ]; then
    report "🔴 임대주택 봇 배포 보류 — 시험 뒤 main 코드가 바뀜($CHANGED). 다시 ./deploy.sh 필요 · $SUMMARY"
    exit 1
  fi
  git merge -q --no-edit origin/main || { git merge --abort; report "🔴 임대주택 봇 배포 중단 — 기록 커밋과 병합 실패 · $SUMMARY"; exit 1; }
done
report "🔴 임대주택 봇 배포 실패 — main 반영 3회 실패 · $SUMMARY"
exit 1
