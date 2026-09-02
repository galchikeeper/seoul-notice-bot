#!/usr/bin/env bash
# 서울 임대주택 공고 알림 봇 — 한 번에 설치
#
#   ./setup.sh <REST_API_KEY> <인가코드>
#
# 브라우저에서 해야 하는 두 가지(카카오 앱 등록, 인가코드 복사)만 끝내두면
# 나머지 — 토큰 발급, 저장소 생성, 코드 푸시, Secrets 등록, 첫 실행 — 는 전부 여기서 한다.
#
# 토큰은 이 스크립트 밖으로 나가지 않는다. GitHub Secrets 에만 저장되고
# 터미널 기록에도 남지 않도록 화면에 출력하지 않는다.

set -euo pipefail

REST_KEY="${1:-}"
CODE="${2:-}"
CLIENT_SECRET="${3:-${KAKAO_CLIENT_SECRET:-}}"   # 콘솔에서 클라이언트 시크릿을 켠 경우
REPO_NAME="${REPO_NAME:-seoul-notice-bot}"
REDIRECT="${REDIRECT:-https://localhost:3000}"

die() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
ok()  { printf '\033[32m✓\033[0m %s\n' "$*"; }
step(){ printf '\n\033[1m[%s]\033[0m %s\n' "$1" "$2"; }

# ── 사전 점검 ────────────────────────────────────────────────
ask() {  # ask <설명> <변수명>  — 입력을 화면에 표시하지 않는다
  local prompt="$1" var="$2" val=""
  printf '\n%s\n> ' "$prompt"
  read -rs val; echo
  [ -n "$val" ] || die "값이 비어 있습니다."
  printf -v "$var" '%s' "$val"
}

# 인자를 안 주면 대화형으로 하나씩 물어본다
if [ -z "$REST_KEY" ]; then
  cat <<'INTRO'

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 서울 임대주택 공고 알림 봇 설치
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 붙여넣는 값은 화면에 보이지 않습니다. 정상입니다.
 붙여넣고 엔터만 누르세요.
INTRO
  ask "1) REST API 키를 붙여넣으세요" REST_KEY
  ask "2) 클라이언트 시크릿을 붙여넣으세요 (안 쓰면 그냥 엔터 대신 아무 값도 없이 Ctrl+C 후 인자로 실행)" CLIENT_SECRET
fi

command -v gh   >/dev/null || die "gh(GitHub CLI)가 없습니다.  brew install gh  후 gh auth login"
command -v git  >/dev/null || die "git 이 없습니다."
command -v curl >/dev/null || die "curl 이 없습니다."
command -v python3 >/dev/null || die "python3 가 없습니다."
gh auth status >/dev/null 2>&1 || die "GitHub 로그인이 필요합니다.  gh auth login"
[ -f bot.py ] && [ -f sources.py ] || die "봇 폴더 안에서 실행해주세요 (bot.py 가 있는 위치)."
ok "사전 점검 통과"

# ── 0. 인가코드 받기 ────────────────────────────────────────
AUTH_URL="https://kauth.kakao.com/oauth/authorize?client_id=${REST_KEY}&redirect_uri=${REDIRECT}&response_type=code&scope=talk_message"
if [ -z "$CODE" ]; then
  step 0/5 "카카오 동의 화면 열기"
  echo "  브라우저가 열립니다. [동의하고 계속하기] 를 누르세요."
  echo "  그러면 '연결할 수 없음' 오류 화면으로 넘어갑니다 — 정상입니다."
  echo "  주소창 전체를 복사해서 아래에 붙여넣으세요 (code= 뒤만 잘라도 됩니다)."
  ( command -v open >/dev/null && open "$AUTH_URL" ) \
    || ( command -v xdg-open >/dev/null && xdg-open "$AUTH_URL" ) \
    || echo "  자동으로 안 열리면 이 주소를 직접 여세요:\n  $AUTH_URL"
  printf '\n3) 넘어간 주소(또는 code 값)를 붙여넣으세요\n> '
  read -r RAW; echo
  CODE="$(printf '%s' "$RAW" | sed -n 's/.*[?&]code=\([^&[:space:]]*\).*/\1/p')"
  [ -n "$CODE" ] || CODE="$(printf '%s' "$RAW" | tr -d '[:space:]')"
  [ -n "$CODE" ] || die "인가코드를 읽지 못했습니다."
  ok "인가코드 확인"
fi

# ── 1. 카카오 토큰 발급 ──────────────────────────────────────
step 1/5 "카카오 리프레시 토큰 발급"
SECRET_ARG=()
[ -n "$CLIENT_SECRET" ] && SECRET_ARG=(-d "client_secret=${CLIENT_SECRET}")
TOKEN_JSON="$(curl -s -X POST "https://kauth.kakao.com/oauth/token" \
  -d "grant_type=authorization_code" \
  -d "client_id=${REST_KEY}" \
  -d "redirect_uri=${REDIRECT}" \
  -d "code=${CODE}" "${SECRET_ARG[@]}")"

REFRESH="$(printf '%s' "$TOKEN_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("refresh_token",""))' 2>/dev/null || true)"
if [ -z "$REFRESH" ]; then
  ERR="$(printf '%s' "$TOKEN_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("error_description") or d.get("error") or "알 수 없는 오류")' 2>/dev/null || echo "응답 해석 실패")"
  echo "  카카오 응답: ${ERR}"
  case "$ERR" in
    *expired*|*invalid_grant*) die "인가코드가 만료됐습니다. 위 주소로 코드를 새로 받아 바로 다시 실행하세요." ;;
    *KOE320*)                  die "인가코드가 이미 사용됐습니다. 새로 받아주세요." ;;
    *client_secret*|*KOE010*)  die "클라이언트 시크릿이 필요합니다. 세 번째 인자로 넘겨주세요:  ./setup.sh <REST키> <코드> <클라이언트시크릿>" ;;
    *)                         die "토큰 발급 실패. REST API 키, Redirect URI(${REDIRECT}) 등록, 클라이언트 시크릿 여부를 확인하세요." ;;
  esac
fi
ok "리프레시 토큰 발급 완료 (화면에는 출력하지 않습니다)"

# ── 2. 발송 테스트 ───────────────────────────────────────────
step 2/5 "카카오톡 발송 테스트"
ACCESS="$(printf '%s' "$TOKEN_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))')"
SEND="$(curl -s -o /dev/null -w '%{http_code}' -X POST "https://kapi.kakao.com/v2/api/talk/memo/default/send" \
  -H "Authorization: Bearer ${ACCESS}" \
  --data-urlencode 'template_object={"object_type":"text","text":"서울 임대주택 알림 봇 설치 완료. 평일 아침에 새 공고를 카드로 보내드립니다.","link":{"web_url":"https://soco.seoul.go.kr"}}')"
[ "$SEND" = "200" ] || die "발송 실패(HTTP ${SEND}). 동의항목에서 '카카오톡 메시지 전송(talk_message)'이 켜져 있는지 확인하세요."
ok "카카오톡으로 테스트 메시지를 보냈습니다 — 지금 확인해보세요"

# ── 3. 저장소 생성 & 푸시 ────────────────────────────────────
step 3/5 "GitHub 저장소 생성 및 코드 푸시"
OWNER="$(gh api user --jq .login)"
if gh repo view "${OWNER}/${REPO_NAME}" >/dev/null 2>&1; then
  ok "저장소가 이미 있습니다: ${OWNER}/${REPO_NAME}"
else
  gh repo create "${REPO_NAME}" --public --description "서울 임대주택 신규 공고 → 카카오톡 카드 알림" >/dev/null
  ok "저장소 생성: ${OWNER}/${REPO_NAME} (공개)"
fi

[ -d .git ] || { git init -q -b main; ok "git 초기화"; }
git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/${OWNER}/${REPO_NAME}.git"
git add -A
git diff --staged --quiet || git commit -qm "서울 임대주택 공고 알림 봇"
git branch -M main
git push -qu origin main
ok "코드 푸시 완료"

# ── 4. Secrets 등록 ─────────────────────────────────────────
step 4/5 "GitHub Secrets 등록"
printf '%s' "$REST_KEY" | gh secret set KAKAO_REST_KEY      --repo "${OWNER}/${REPO_NAME}"
printf '%s' "$REFRESH"  | gh secret set KAKAO_REFRESH_TOKEN --repo "${OWNER}/${REPO_NAME}"
ok "KAKAO_REST_KEY, KAKAO_REFRESH_TOKEN 등록"
if [ -n "$CLIENT_SECRET" ]; then
  printf '%s' "$CLIENT_SECRET" | gh secret set KAKAO_CLIENT_SECRET --repo "${OWNER}/${REPO_NAME}"
  ok "KAKAO_CLIENT_SECRET 등록"
fi

if [ -n "${GH_PAT:-}" ]; then
  printf '%s' "$GH_PAT" | gh secret set GH_PAT --repo "${OWNER}/${REPO_NAME}"
  ok "GH_PAT 등록 — 리프레시 토큰이 자동 갱신됩니다"
else
  printf '  \033[33m·\033[0m GH_PAT 없음 — 두 달쯤 뒤 토큰 재발급이 필요하고, 그때 봇이 카톡으로 알려줍니다\n'
  printf '    자동 갱신을 원하면:  GH_PAT=<repo 권한 토큰> ./setup.sh ...\n'
fi

# ── 5. 첫 실행 ──────────────────────────────────────────────
step 5/5 "첫 실행 (카톡 발송 없이 동작만 확인)"
sleep 3
gh workflow run notice.yml --repo "${OWNER}/${REPO_NAME}" -f dry_run=1 >/dev/null 2>&1 \
  && ok "테스트 실행을 걸었습니다" \
  || printf '  \033[33m·\033[0m 자동 실행 실패 — Actions 탭에서 직접 Run workflow 하세요\n'

cat <<EOF

────────────────────────────────────────────
 설치 완료
────────────────────────────────────────────
 저장소   https://github.com/${OWNER}/${REPO_NAME}
 실행기록 https://github.com/${OWNER}/${REPO_NAME}/actions
 주기     평일 08:00 KST 자동

 다음에 할 것
  1. Actions 탭에서 방금 건 실행이 초록불인지 확인
  2. 카드가 실제로 만들어지는지 보려면 Run workflow →
     rewind 에 6642 를 넣고 실행 (최근 공고 3건을 신규로 취급)
────────────────────────────────────────────
EOF
