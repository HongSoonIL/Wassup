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
