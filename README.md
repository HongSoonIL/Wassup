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
python manage.py runserver           # http://127.0.0.1:8000/admin/
python manage.py test                # 테스트
```

`git pull` 뒤에 새 마이그레이션이 있으면 `python manage.py migrate`를 다시 실행한다.

## 1단계 API (설계서 3.6절)

화면용 API는 세션 로그인을 쓴다. 로그인 안 하면 401, 남의 채팅방이면 403, 없는 채팅방이면 404다.

| Method | URL | 하는 일 | 성공 | 오류 |
|---|---|---|---|---|
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

아직 로그인 API가 없어서 admin 로그인 세션을 빌려 쓴다.

1. `/admin/`에 superuser로 로그인한다.
2. admin의 **친구 관계(채팅방)** 에서 내 계정과 도현을 친구로 추가한다.
3. 같은 브라우저에서 `/api/friends/`와 `/api/rooms/{id}/messages/`를 연다.

## 주간 승인과 실제 친구 피드

Django의 `/studio/`에서 일주일의 후보를 검토해 묶음 승인하고, `/feed/`에서 로그인한 사용자의 친구가 발행한 소식을 읽는다. 두 화면 모두 `/login/`에서 같은 세션으로 로그인한다. DB 연결과 마이그레이션을 준비한 뒤 아래 명령을 실행한다.

```sh
python manage.py migrate
python manage.py seed_world
python manage.py seed_publication
(cd frontend && pnpm install --frozen-lockfile && pnpm build)
python manage.py runserver
```

별도 터미널에서 `python manage.py run_publication_worker`를 실행한다. `--once`는 대기 중인 주간 생성과 시각이 된 발행을 한 차례 처리한다. 운영 계정은 superuser 또는 `world.publish_week` 권한이 있는 사용자다. 일반 계정은 Django admin에서 도현이나 서윤과 친구 관계를 맺은 뒤 피드를 확인한다.

- **주간 계획.** 운영자가 월요일을 선택해 열면 워커가 가상 학교 달력으로 후보를 만든다. 첫 구현은 시험·체육대회·방학·평상시의 고정 문구와 기존 사진을 사용한다. 시드는 2027년 4월 19일 주간의 중간고사와 체육대회를 포함한다. 다른 주의 행사는 admin의 School events에서 등록한다.
- **운영 검토.** 사진 없는 후보는 수정하거나 제외한 뒤 승인한다. 실제 시계 기준 과거 시각은 승인할 수 없다. 일정 변경 시 해당 행사를 참조하는 미발행 후보만 재검토하며, 실패한 사진은 자산 복구 후 같은 카드에서 재시도한다.
- **공개.** 친구 관계와 발행 성공 시각을 확인한 후 피드·프로필 API·사진을 제공한다. 발행 사진은 `POST_PHOTO_DIR`(기본 `private-media/`)에 보관한다. 웹 서버에서 이 디렉터리를 정적 경로로 공개하지 않는다. 새로고침이나 탭 복귀로 최신 피드를 읽는다.
- **검증.** PostgreSQL에서 `python manage.py test dashboard.test_publication`으로 두 계정의 접근 범위, 일정 변경, 동시 승인·중복 실행, 오류 후 재시도를 확인한다. 이후 사진 인용 대화와 모델 연결의 계약은 [시험 주간 통합 설계](docs/design/exam-week.md)에 있다.

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
