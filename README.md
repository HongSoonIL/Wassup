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
2. `/discover/`에서 첫 친구를 추가하고 프로필의 **대화하기**를 누른다.
3. 다시 `/`를 열면 친구 홈에서 마지막 대화와 안 읽은 답장을 확인할 수 있다. API로 확인하려면 `/api/friends/`의 `room_id`로 `/api/rooms/{id}/messages/`를 연다.

## 주간 승인과 실제 친구 피드

Django의 `/studio/`에서 일주일의 후보를 검토해 묶음 승인하고, `/feed/`에서 로그인한 사용자의 친구가 발행한 소식을 읽는다. 두 화면 모두 `/login/`에서 같은 세션으로 로그인한다. DB 연결과 마이그레이션을 준비한 뒤 아래 명령을 실행한다.

```sh
python manage.py migrate
python manage.py seed_world
python manage.py seed_publication
(cd frontend && pnpm install --frozen-lockfile && pnpm build)
python manage.py runserver
```

별도 터미널에서 `python manage.py run_publication_worker`를 실행한다. `--once`는 대기 중인 주간 생성과 시각이 된 발행을 한 차례 처리한다. 운영 계정은 superuser 또는 `world.publish_week` 권한이 있는 사용자다. 일반 계정은 친구 찾기에서 도현이나 서윤을 추가한 뒤 `/feed/`에서 해당 친구의 공개 소식을 확인한다.

- **주간 계획.** 운영자가 월요일을 선택해 열면 워커가 가상 학교 달력으로 후보를 만든다. 실제 모델 모드는 계절, 학사 일정, 캐릭터별 일과와 등록 사진, 직전 4주간 공개한 글로 친구 한 명당 후보 2~3건을 제안한다. 같은 주차를 다시 열면 기존 편집본을 읽는다. 생성 실패 시 해당 주차에서 재시도한다. 시드는 2027년 4월 19일 주간의 중간고사와 체육대회를 포함한다. 다른 주의 행사는 admin의 School events에서 등록한다.
- **운영 검토.** 같은 사진이 한 주에 여러 번 추천되면 첫 후보에만 붙인다. 사진 없는 후보에는 운영자가 사진을 선택하거나 그 주 발행에서 제외한 뒤 승인한다. 실제 시계 기준 과거 시각은 승인할 수 없다. 일정 변경 시 해당 행사를 참조하는 미발행 후보만 재검토하며, 실패한 사진은 자산 복구 후 같은 카드에서 재시도한다.
- **공개.** 친구 관계와 발행 성공 시각을 확인한 후 피드·프로필 API·사진을 제공한다. 발행 사진은 `POST_PHOTO_DIR`(기본 `private-media/`)에 보관한다. 웹 서버에서 이 디렉터리를 정적 경로로 공개하지 않는다. 새로고침이나 탭 복귀로 최신 피드를 읽는다.
- **검증.** PostgreSQL에서 `python manage.py test dashboard.test_publication`으로 두 계정의 접근 범위, 일정 변경, 동시 승인·중복 실행, 오류 후 재시도를 확인한다. 사진 인용 대화와 모델 연결의 설계는 [시험 주간 통합 설계](docs/design/exam-week.md)에 있다.

## 피드 사진으로 대화하기

친구 계정으로 `/feed/`에서 사진이나 ‘말 걸기’를 누르면 기존 방의 입력창에 그 게시물이 붙는다. 전송 전에는 인용을 해제할 수 있다. 헤더의 이름·사진 또는 말풍선 옆 얼굴을 누르면 그 친구의 프로필과 공개 사진을 볼 수 있다.

웹 서버와 별도 터미널에서 `python manage.py run_reply_worker`를 실행한다. 기본값 `LLM_BACKEND=fake`에서는 키 없이 검수된 샘플 문구를 사용한다. 실제 모델을 쓰려면 서버의 `.env`에 `LLM_BACKEND=openai`, `LLM_MODEL`(구조화 출력을 지원하는 공통 모델 ID), `LLM_API_KEY`를 설정한다. 채팅·기억 추출·주간 발행의 모델은 각각 `LLM_CHAT_MODEL`, `LLM_MEMORY_MODEL`, `LLM_WEEKLY_MODEL`로 덮어쓸 수 있다. 같은 접두사의 `_EFFORT`에 모델이 지원하는 추론 강도를 넣거나, 모델 기본값을 쓰려면 비워 둔다. 설정을 바꾼 뒤 웹 서버와 해당 워커를 재시작한다. 일과에 맞춘 읽음·발송 시각을 사용하려면 `WASSUP_REPLY_DEMO_SECONDS=0`으로 둔다.

베타의 채팅과 기억 추출 설정은 `.env.example`에 있다. 기존 `.env`에 적용하려면 `LLM_CHAT_MODEL=gpt-6.2-sol`, `LLM_MEMORY_MODEL=gpt-6-luna`와 두 기능의 `_EFFORT=low`를 추가한다. 친구별 기억은 사용자가 해당 방에서 켜야 시작한다.

답장 워커는 해당 방의 한국 시각 기준 당일 메시지 중 최근 16개, 캐릭터 설정·현재 일과, 최근 공개 게시물 최대 4건 및 인용한 공개본을 텍스트로 보낸다. 호출은 `store: false`로 구성한다. 공급자 오류나 발송 기한 초과 시 검수된 답장을 보낸다. 여러 말풍선은 한 번에 생성한 뒤 서버가 하나씩 전송한다. 상대가 중간에 메시지를 보내면 남은 말풍선을 취소하고 이미 도착한 대화에 이어서 답한다. [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)

## 친구별 대화 기억

채팅방에서 친구 이름을 누르고 프로필의 **대화 기억**을 열면 이 친구의 기억을 켤 수 있다. 기존 계정과 새 계정 모두 친구마다 꺼진 상태로 시작한다. 켠 뒤 내가 직접 말한 일정과 취향을 기억할 수 있고, 같은 화면에서 원본 메시지 확인·수정·삭제가 가능하다. 끄기를 확인하면 해당 방의 저장된 기억을 지운다. 채팅 내역은 계속 열람할 수 있다.

웹 서버와 답장 워커에 같은 환경 설정을 적용한 별도 터미널에서 `python manage.py run_memory_worker`를 실행한다. 배포 시 먼저 `python manage.py migrate`를 실행한다. 추출은 메시지 저장 후 별도 작업에서 수행한다. 실제 모델 모드는 추출을 위해 방금 보낸 메시지를 모델에 전송한다. 공개 피드와 주간 발행에는 이 기억을 사용하지 않는다.

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
