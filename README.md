# 서울 임대주택 공고 알림 봇

서울 공공지원민간임대·청년안심주택의 신규 모집공고를 평일 아침에 찾아
**카카오톡으로 카드 이미지째** 보내준다. GitHub Actions 에서 돌기 때문에
내 컴퓨터가 꺼져 있어도 동작한다.

```
소스 확인 → 신규 판정 → 공고문 PDF 파싱 → 카드 PNG 렌더
        → 저장소 커밋(=이미지 공개 주소 확보) → 카카오톡 피드 발송
```

## 감시 대상

| 소스 | 읽는 방법 |
|---|---|
| 서울시 청년안심주택 모집공고 | 게시판이 쓰는 JSON API 직접 호출 |
| 힐스테이트 관악 뉴포레 공지사항 | HTML 파싱, 글 개수 변화 감지 |
| 공간지원리츠 5개 단지 | 단지별 공지 게시판 각각 파싱 |

마이홈포털은 robots 정책으로 접근이 막혀 제외했다. 여기 뜨는 서울 공고는
결국 단지 홈페이지에도 올라오므로 실질 공백은 거의 없다.

## 파일

| 파일 | 역할 |
|---|---|
| `bot.py` | 전체 흐름 |
| `sources.py` | 공고 수집 |
| `pdfparse.py` | 공고문 PDF에서 평형·보증금·월세 추출 |
| `card.py` | 카드 PNG 렌더 (Playwright + QR) |
| `kakao.py` | 토큰 갱신 + 피드 템플릿 발송 |
| `state.json` | 마지막 확인 지점 (매 실행마다 갱신·커밋) |
| `cards/` | 생성된 카드 이미지 (jsDelivr 로 공개 서빙) |

---

# 설치

## 1. 저장소 만들기

**공개(Public) 저장소**여야 한다. 카카오가 이미지를 가져가려면 주소가 공개여야
하기 때문이다. 카드에는 공개된 모집공고 정보만 들어가고 개인정보는 없다.

```bash
gh repo create seoul-notice-bot --public --clone
# 이 폴더의 파일을 전부 복사한 뒤
git add . && git commit -m "init" && git push
```

## 2. 카카오 개발자 앱 등록 (무료)

1. https://developers.kakao.com → 내 애플리케이션 → **애플리케이션 추가하기**
2. 앱 이름 아무거나, 회사명은 본인 이름
3. **앱 키** 화면에서 `REST API 키` 복사 → 나중에 `KAKAO_REST_KEY` 로 쓴다
4. **카카오 로그인** 메뉴 → 활성화 **ON**
5. 같은 화면 **Redirect URI** 에 `https://localhost:3000` 등록
6. **동의항목** 메뉴 → `카카오톡 메시지 전송 (talk_message)` → **필수 동의**로 설정
7. **플랫폼** 메뉴 → Web 플랫폼 등록 → 사이트 도메인에 `https://cdn.jsdelivr.net` 추가
   (카드 이미지를 여기서 서빙한다)

## 3. 토큰 발급

### 3-1. 인가 코드 받기

아래 주소의 `{REST_API_KEY}` 를 바꿔서 브라우저 주소창에 붙여넣는다.

```
https://kauth.kakao.com/oauth/authorize?client_id={REST_API_KEY}&redirect_uri=https://localhost:3000&response_type=code&scope=talk_message
```

동의하면 `https://localhost:3000/?code=XXXXX` 로 넘어간다. 페이지는 안 열려도
된다. **주소창의 `code=` 뒤 값**만 복사한다. 이 코드는 몇 분 안에 만료되니
바로 다음 단계로 간다.

### 3-2. 리프레시 토큰 받기

터미널에서 (`{REST_API_KEY}`, `{CODE}` 교체):

```bash
curl -X POST "https://kauth.kakao.com/oauth/token" \
  -d "grant_type=authorization_code" \
  -d "client_id={REST_API_KEY}" \
  -d "redirect_uri=https://localhost:3000" \
  -d "code={CODE}"
```

응답의 **`refresh_token`** 값을 복사한다. (`access_token` 은 6시간짜리라
저장할 필요 없다. 봇이 매번 리프레시 토큰으로 새로 받는다.)

## 4. GitHub Secrets 등록

저장소 → Settings → Secrets and variables → Actions → New repository secret

| 이름 | 값 | 필수 |
|---|---|---|
| `KAKAO_REST_KEY` | 2단계의 REST API 키 | 필수 |
| `KAKAO_REFRESH_TOKEN` | 3단계의 refresh_token | 필수 |
| `GH_PAT` | `repo` 권한 Personal Access Token | 선택 |

`GH_PAT` 는 리프레시 토큰이 새로 발급될 때 자동으로 Secret 을 갱신하는 용도다.
넣지 않으면 두 달에 한 번쯤 3단계를 다시 해야 하고, 그때가 오면 봇이
카카오톡으로 알려준다.

## 5. 첫 실행

저장소 → Actions → **서울 임대주택 공고 알림** → Run workflow

- `dry_run` 에 `1` → 카톡 발송 없이 동작만 확인
- `rewind` 에 `6642` → 최근 공고 3건을 신규로 취급해 카드를 실제로 만들어 봄

정상이면 이후 **평일 08:00 KST 에 자동 실행**된다.

---

# 동작 방식 메모

**신규 판정은 `boardId` 로 한다.** 게시판 화면의 번호(469 등)는 전체 건수에서
역산한 값이고, JSON 의 `rnum` 은 페이지 안 순번(1~10)일 뿐이다. 증가하는 고유
키는 `boardId` 하나뿐이라 이것만 믿는다.

**공고문 PDF 파싱은 최선 노력이다.** 청년안심주택 공고문은 표를 선 없이 그려서
일반적인 표 추출로는 안 잡힌다. 그래서 텍스트 줄을 창 단위로 훑어
`청년 309 5,700 50 7,600 43 9,500 36` 같은 줄에서 (보증금, 월세) 쌍을 뽑고,
앞뒤 줄에서 전용면적을 찾는다. 사업자마다 서식이 달라 실패할 수 있고,
그때는 카드에 "공고문 PDF 확인"으로 표시된다. 값이 틀려 보이면 항상 공고문 원문이
기준이다.

**보증금·월세는 범위로 표시된다.** 보증금 비율을 30/40/50% 중 선택할 수 있어서,
보증금을 올리면 월세가 내려간다. 카드의 `5,700~9,500 / 36~50` 은
"보증금 5,700만이면 월 50만, 9,500만이면 월 36만"이라는 뜻이다.

**카드 이미지는 저장소에 쌓인다.** 커밋된 파일을 jsDelivr CDN 으로 서빙해
카카오에 넘긴다. 60일 지난 카드는 워크플로가 자동으로 지운다.

# 손보고 싶을 때

| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 카드 개수 조절 | 워크플로의 `CARD_LIMIT` |
| 실행 시각 변경 | 워크플로 `cron` (UTC 기준, KST-9시간) |
| 우선 알림 단지 추가 | `sources.py` 의 `FAST_TURNOVER` |
| 감시 단지 추가 | `sources.py` 의 `SPACEREITS` |
| 카드 디자인 | `card.py` 의 `build_html` |
