# Wassup
어딘가 살고 있는 AI 친구와 카톡처럼 대화하는 메신저 웹 서비스

## 처음 세팅 (Mac)

```bash
git clone https://github.com/HongSoonIL/Wassup.git
cd Wassup

brew install python@3.13
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env에서 DJANGO_SECRET_KEY와 POSTGRES_PASSWORD를 새 값으로 바꾼다 (.env.example 주석 참고)

brew install --cask docker-desktop   # 처음 한 번, 설치 후 Docker Desktop을 실행해 둔다
docker compose up -d                 # PostgreSQL + Redis
docker compose ps                    # db, redis가 (healthy)면 성공

python manage.py migrate
python manage.py seed_world          # 도현·서윤 캐릭터 넣기 (여러 번 실행해도 됨)
python manage.py createsuperuser     # admin 로그인용
```

## 매일 쓰는 명령어

```bash
source .venv/bin/activate            # 새 터미널마다
docker compose up -d                 # Docker Desktop을 껐다 켰다면
python manage.py runserver           # http://127.0.0.1:8000/
python manage.py test                # 테스트
```

`git pull` 뒤에 새 마이그레이션이 있으면 `python manage.py migrate`를 다시 실행한다.

## 1단계 API (설계서 3.6절)

화면용 API는 세션 로그인을 쓴다. 로그인 안 하면 401, 남의 채팅방이면 403, 없는 채팅방이면 404다.

| Method | URL | 하는 일 | 성공 | 오류 |
|---|---|---|---|---|
| GET | `/api/characters/` | 친구 찾기 목록과 내 기존 방 번호 | 200 | 401 |
| POST | `/api/friends/` | `{"character_id": 1}`로 친구 추가, 기존 방이 있으면 그 번호 반환 | 201, 200 | 400, 401, 403, 404 |
| GET | `/api/friends/` | 친구 목록 (마지막 메시지, 안 읽은 수) | 200 | 401 |
| POST | `/api/rooms/{id}/messages/` | 메시지 보내기 `{"text": "..."}` | 201 | 400, 401, 403, 404 |
| GET | `/api/rooms/{id}/messages/?before=&limit=` | 이전 대화 불러오기 (기본 30개, 최대 100개) | 200 | 400, 401, 403, 404 |

- 채팅방 번호(`room_id`)는 친구 관계(`Friendship`) 번호와 같다.
- 메시지 보내기 응답에는 답장이 담기지 않는다. 답장은 2단계부터 WebSocket 이벤트로 온다.
- **CSRF:** GET 응답이 `csrftoken` 쿠키를 심는다. POST할 때는 그 값을 `X-CSRFToken` 헤더에 담아야 한다.

```js
const csrftoken = document.cookie.match(/csrftoken=([^;]+)/)[1];
await fetch(`/api/rooms/${roomId}/messages/`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrftoken},
  body: JSON.stringify({text: '뭐해?'}),
});
```

### 브라우저로 직접 확인하기

채팅과 피드는 Django 세션을 공유한다.

1. 아래 실행 절차대로 화면을 빌드한 뒤 `/`를 열고 **가입하기**를 선택한다. 기존 계정은 같은 화면에서 로그인한다.
2. 피드의 **첫 친구 만나기** 또는 **내 프로필**(`/me/`)의 **친구 찾기**에서 첫 친구를 추가한다. 친구 프로필의 **일상 기록**에서 사진 아래 **답장**을 누르면 그 게시물을 인용해 대화를 시작한다.
3. 다시 `/`를 열면 친구 피드가 열린다. **메시지**(`/messages/`)에서 마지막 대화와 안 읽은 답장을 확인하고, 친구를 선택해 대화를 이어간다. API로 확인하려면 `/api/friends/`의 `room_id`로 `/api/rooms/{id}/messages/`를 연다.

## 주간 승인과 실제 친구 피드

`world.publish_week` 권한이 있는 운영자는 `/studio/`에 직접 접속해 일주일의 발행 후보를 검토하고 승인한다. 사용자는 `/feed/`에서 친구가 발행한 소식을 읽는다. 두 화면 모두 Django 세션 로그인을 사용한다. DB 연결과 마이그레이션을 준비한 뒤 아래 명령을 실행한다.

```sh
python manage.py migrate
python manage.py seed_world
python manage.py seed_publication
(cd frontend && pnpm install --frozen-lockfile && pnpm build)
python manage.py runserver
```

별도 터미널 두 곳에서 `python manage.py run_publication_worker`와 `python manage.py run_photo_worker`를 실행한다. 첫 워커는 주간 초안과 예약 발행을, 두 번째 워커는 사진 생성을 처리한다. 각 명령에 `--once`를 붙이면 대기 중인 작업을 한 차례 처리한 후 종료한다. 운영 계정은 superuser 또는 `world.publish_week` 권한이 있는 사용자다.

- **주간 계획.** 금요일 오후 6시(한국 시간)부터 다음 월요일 주차를 준비한다. 워커가 쉬었다가 복귀하면 발행할 시간이 남은 주차를 확인한다. 운영자도 월요일 날짜를 선택해 미리 열 수 있다. 학교 달력, 일과, 계절, 직전 4주간의 공개 기록을 바탕으로 도현·서윤 각각 0~3건을 제안한다. 시험·방학, 친구별 일과와 최근 게시 빈도에 따라 주차별 상한이 달라진다. 조용한 주는 빈 계획으로 검토할 수 있다.
- **사진.** 장면에 맞는 캐릭터의 등록 사진을 우선 선택한다. 사진이 필요한 새 초안은 비공개 생성 대기열로 보낸다. 두 친구의 자동 생성은 한 주 최대 6장이다. 생성 중이던 장면을 수정하거나 제외하면 뒤늦게 도착한 사진은 연결하지 않는다. `LLM_BACKEND=openai`에서 `LLM_WEEKLY_MODEL=gpt-6.2-sol`, `LLM_WEEKLY_EFFORT=low`, `WASSUP_IMAGE_MODEL=gpt-image-2.6-flare`로 실행한다. 실제 이미지 생성에는 서버의 API 키와 해당 이미지 모델 사용 권한이 필요하다. 개발용 `fake` 모드에서는 외부 호출 없이 흐름을 확인할 수 있다.
- **운영 검토.** 스튜디오에서 사진의 실제 모습과 설명, 사건 시각, 발행 시각, 친구가 기억할 공개 사실을 함께 확인한다. 필요한 후보는 수정·사진 교체·재생성하고, 이번 주에 올리지 않을 후보는 제외한다. 묶음 승인 후 예약 시각에 발행된다. 2027년 4월 19일 주간의 중간고사와 체육대회는 `seed_publication`으로 등록할 수 있다.
- **공개.** 친구 관계와 발행 성공 시각을 확인한 후 피드·프로필 API·사진을 제공한다. 발행 사진은 `POST_PHOTO_DIR`(기본 `private-media/`)에 보관한다. 웹 서버에서 이 디렉터리를 정적 경로로 공개하지 않는다. 새로고침이나 탭 복귀로 최신 피드를 읽는다.
- **검증.** PostgreSQL에서 `python manage.py test world.test_weekly_generation world.test_photo_jobs dashboard.test_publication`으로 주차 준비, 사진 생성 실패와 재시도, 두 계정의 접근 범위, 동시 승인과 중복 실행을 확인한다. 사진 인용 대화와 모델 연결의 설계는 [시험 주간 통합 설계](docs/design/exam-week.md)에 있다.

## 피드 사진으로 대화하기

이어지는 일이 있는 글에는 **이전 소식 보기**가 표시된다. 피드나 프로필에서 펼쳐 이전 사진과 문구를 읽고, 현재 글의 ‘답장’를 눌러 두 장면에 관해 물을 수 있다. 운영자는 주간 스튜디오에서 현재 초안과 이전 글을 비교하고 연결을 바꾸거나 해제할 수 있다. 후보는 같은 친구가 해당 주 시작 전 4주 동안 공개한 글에서 고른다. 이전 글이 철회되거나 수정되면 해당 버전의 연결은 사라진다.

친구 계정으로 `/feed/`에서 사진을 누르면 원본이 열리고, ‘답장’를 누르면 기존 방의 입력창에 그 게시물이 붙는다. 전송 전에는 인용을 해제할 수 있다. 헤더의 이름·사진 또는 말풍선 옆 얼굴을 누르면 그 친구의 프로필과 공개 사진을 볼 수 있다.

채팅에서 “두부 사진 보여줘”라고 하면, 서윤은 자기가 공개한 사진 중 두부가 보이는 사진을 답장에 한 장 붙일 수 있다. “오늘 찍은 거 있어?”에는 게시일과 별도로 기록한 사건 날짜를 사용한다. 사진이 도착하면 눌러 확대하거나 “이거 언제 찍었어?”라고 이어서 물을 수 있다. 같은 방에서 이미 받은 사진도 다시 보내 달라고 할 수 있다. 웹 서버와 답장 워커를 시작하기 전에 `python manage.py migrate`로 첨부 필드를 적용한다.

웹 서버와 별도 터미널에서 `python manage.py run_reply_worker`를 실행한다. 기본값 `LLM_BACKEND=fake`에서는 키 없이 검수된 샘플 문구를 사용한다. 실제 모델을 쓰려면 서버의 `.env`에 `LLM_BACKEND=openai`, `LLM_MODEL`(구조화 출력을 지원하는 공통 모델 ID), `LLM_API_KEY`를 설정한다. 채팅·기억 추출·주간 발행의 모델은 각각 `LLM_CHAT_MODEL`, `LLM_MEMORY_MODEL`, `LLM_WEEKLY_MODEL`로 덮어쓸 수 있다. 같은 접두사의 `_EFFORT`에 모델이 지원하는 추론 강도를 넣거나, 모델 기본값을 쓰려면 비워 둔다. 설정을 바꾼 뒤 웹 서버와 해당 워커를 재시작한다. 일과에 맞춘 읽음·발송 시각을 사용하려면 `WASSUP_REPLY_DEMO_SECONDS=0`으로 둔다.

베타의 채팅과 기억 추출 설정은 `.env.example`에 있다. 기존 `.env`에 적용하려면 `LLM_CHAT_MODEL=gpt-6.2-sol`, `LLM_MEMORY_MODEL=gpt-6-luna`와 두 기능의 `_EFFORT=low`를 추가한다. 친구별 기억은 사용자가 해당 방에서 켜야 시작한다.

답장 워커는 해당 방의 한국 시각 기준 당일 메시지 중 최근 16개, 캐릭터 설정·현재 일과, 최근 공개 게시물 최대 4건 및 인용한 공개본을 텍스트로 보낸다. 호출은 `store: false`로 구성한다. 공급자 오류나 발송 기한 초과 시 검수된 답장을 보낸다. 여러 말풍선은 한 번에 생성한 뒤 서버가 하나씩 전송한다. 상대가 중간에 메시지를 보내면 남은 말풍선을 취소하고 이미 도착한 대화에 이어서 답한다. [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)

## 내 취향과 친구별 대화 기억

**내 프로필 → 내 취향**에서 관심 태그와 한 줄을 최대 다섯 개까지 저장할 수 있다. 태그만 고르거나 한 줄만 써도 된다. 이 목록은 로그인한 사용자의 모든 AI 친구가 답장할 때 참고한다. **내 프로필 → 친구별 기억**에서는 선택한 친구와의 대화에서 저장한 기억을 확인하고 관리한다.

채팅방에서 친구 이름을 누르고 프로필에서 **대화 설정과 기억 → 대화 기억**을 열면 이 친구의 기억을 켤 수 있다. 기존 계정과 새 계정 모두 친구마다 꺼진 상태로 시작한다. 켠 뒤 내가 직접 말한 일정과 취향을 기억할 수 있고, 같은 화면에서 원본 메시지 확인·수정·삭제가 가능하다. 끄기를 확인하면 해당 방의 저장된 기억을 지운다. 채팅 내역은 계속 열람할 수 있다.

웹 서버와 답장 워커에 같은 환경 설정을 적용한 별도 터미널에서 `python manage.py run_memory_worker`를 실행한다. 배포 시 먼저 `python manage.py migrate`를 실행한다. 추출은 메시지 저장 후 별도 작업에서 수행한다. 실제 모델 모드는 추출을 위해 방금 보낸 메시지를 모델에 전송한다. 공개 피드와 주간 발행에는 이 기억을 사용하지 않는다.

## 친구가 먼저 연락하는 경우

친구 프로필의 **대화 설정과 기억**에서 **먼저 연락받기**와 **대화 기억**을 모두 켜면, 친구는 전날의 일정에 관해 먼저 말을 걸 수 있다. 예를 들어 도현에게 통계 시험이 있다고 말했고 그 결과를 아직 이야기하지 않았다면, 이튿날 도현의 한가한 시간에 한 번 물어본다. 한국 시각 10시부터 21시 전까지, 모든 친구를 합쳐 사용자 한 명당 하루 한 건까지 채팅방에 도착한다.

웹 서버와 같은 설정으로 `python manage.py migrate`를 실행하고 기존 `run_reply_worker`를 재시작한다. 일정 기억의 자동 추출에는 `run_memory_worker`도 필요하다. 기존 친구와 새 친구 모두 먼저 연락받기가 꺼진 상태에서 시작한다. 친구 삭제를 확인하면 그 친구와의 대화·기억·예약도 지워진다.

합성 대화로 선톡 품질을 비교하려면 `python -m llm.proactive_eval --backend openai --output followups.json`을 실행한다. 한 실행에 최대 여섯 건을 호출하며, 결과에는 모델 설정·프롬프트 지문·보내야 하는지에 대한 예상값·실제 한국어 문구가 남는다. `--model`과 `--effort`로 비교 조건을 바꿀 수 있다.

## 첫 베타 화면 시안

`frontend`에서 `pnpm install --frozen-lockfile`과 `pnpm dev`를 실행하고 `/design.html`을 연다. 친구 홈·프로필·피드·채팅과 운영 스튜디오가 도현·서윤의 같은 하루를 사용한다. 새로고침하면 샘플이 초기화된다. 검토 시각, 계정 전환과 예약 발행 재현 방법은 [첫 베타 제품 설계](docs/design/first-beta.md)에 있다.

## 앱 구성 (설계서 3.2절)

| 앱 | 하는 일 | 1단계 모델 |
|---|---|---|
| accounts | 가입, 연령 확인, 설정 | User |
| world | 공개 프로필, 피드, 일과, 일기, 사진 | Character, Persona, RoutineTemplate |
| chat | 친구 관계, 메시지, 답장 계획, 실시간 이벤트 | Friendship, Message, ReplyPlan |
| memory | 기억과 검색 | (3단계) |
| llm | LLM 게이트웨이, 프롬프트 조립, 출력 검사 | 모델 없음 |
| dashboard | 관리자 화면, 초안 승인, 모니터링 | 모델 없음 |

앱끼리 import가 순환하지 않도록 참조 방향을 지킨다.

```
chat  ──▶ world, llm
llm   ──▶ world, memory
world ──✗──▶ chat    (거꾸로는 금지)
```

## 규칙

- 시각은 `timezone.now()` 대신 `world.clock.world_now()`를 쓴다. 발표 때 세계 시계를 옮기기 위해서다.
- `.env`는 커밋하지 않는다. 새 설정값이 생기면 `.env.example`에 같이 적는다.
- 작업은 `dev` 또는 기능별 브랜치에서 하고 PR로 `main`에 합친다.

## 팀 서버 배포

루트 Dockerfile은 React 정적 자산과 Django ASGI 서버를 한 이미지로 빌드한다. 웹과 네 워커가 PostgreSQL, Redis, 비공개 S3 호환 버킷을 공유한다.

| 서비스 | WASSUP_ROLE | 실행할 일 |
| --- | --- | --- |
| web | web | HTTPS, 세션, WebSocket, 정적 자산과 권한 검사 후 사진 응답 |
| reply | reply | 답장 생성·읽음·입력·말풍선 전달과 먼저 연락하기 |
| memory | memory | 동의한 방의 기억 추출 |
| publication | publication | 다음 주 준비와 운영자가 승인한 게시물 발행 |
| photo | photo | 발행 후보 사진 생성 |

1. 각 서비스에 같은 Dockerfile을 지정한다. 웹에만 공개 도메인과 포트를 연결하고 웹의 배포 전 명령을 `sh deploy/release`, 준비 상태 경로를 `/health/ready/`로 설정한다. 첫 배포에서는 웹의 준비 상태를 확인한 뒤 워커를 올린다. 배포 전 명령은 마이그레이션과 초기 캐릭터·사진 등록을 수행하며 기존 캐릭터 편집은 보존한다.
2. 모든 서비스에 `DATABASE_URL`, `REDIS_URL`, 같은 `DJANGO_SECRET_KEY`, 사진 버킷 변수와 용도별 모델 설정을 연결한다. Railway 버킷의 BUCKET, ENDPOINT, REGION, ACCESS_KEY_ID, SECRET_ACCESS_KEY를 각각 .env.example의 AWS 변수에 연결한다. 신규 Railway 버킷은 virtual 방식을 쓰고 기존 path 방식 버킷은 `AWS_S3_ADDRESSING_STYLE=path`를 지정한다. Django 관리자에서 올린 캐릭터 프로필 사진은 만료되는 서명 URL로, 개인 사진 및 발행 사진은 권한을 검사하는 앱 경로로 전달한다.
3. Railway에서 팀 모드가 자동 활성화된다. 다른 호스팅에서는 `WASSUP_TEAM=1`, `DJANGO_ALLOWED_HOSTS`를 지정하고 HTTPS 프록시가 X-Forwarded-Proto를 직접 설정하도록 구성한다. 팀 모드는 보안 쿠키, 실시간 한국 시각과 초대 가입을 사용한다.
4. 웹 컨테이너에서 `python manage.py create_invite`로 가입자별 초대 코드를 발급한다. 가입 화면에서 한 번 쓸 수 있고 7일 후 만료된다. 발행 담당자에게는 Django의 `world.publish_week` 권한을 부여한다. 담당자는 `/studio/`에서 후보 문구·사진·일정을 확인하고 승인한다.
5. 재배포 후 웹 준비 상태와 네 워커의 실행 상태를 확인한다. 이전 계정으로 로그인해 기존 사진, 답장 기록과 예약을 확인한다. 코드 롤백 시에는 기존 DB 및 버킷을 유지하고 해당 릴리스와 스키마가 호환되는지 확인한다.

같은 커밋을 수동으로 다시 배포할 때는 Railway CLI v5에서 저장소 루트를 `railway link`로 대상 프로젝트에 연결한다. 아래 명령은 현재 커밋에 기록된 파일만 임시 폴더에 풀어 웹에 배포한다.

```sh
revision=$(git rev-parse HEAD)
release_dir=$(mktemp -d)
git archive "$revision" | tar -x -C "$release_dir"
railway up "$release_dir" --path-as-root --environment production --service web --detach --message "$revision"
railway service status --environment production --service web
```

해당 웹 배포가 `SUCCESS`이고 `/health/ready/`가 200을 반환하면 같은 폴더를 사용해 `--service`를 각각 `reply`, `memory`, `publication`, `photo`로 바꿔 배포한다. 각 서비스의 배포 기록에서 같은 커밋 번호를 확인할 수 있다.
