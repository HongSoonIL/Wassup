# 시험 주간 발행과 사진 인용 API

첫 통합에서 구현할 계약이다. 기준 데이터는 2027년 4월 19일 월요일에 시작하는 12번 주차다. A의 서윤 방은 31, B의 서윤 방은 32, A의 도현 방은 33이다. 공개 게시물 501과 자산은 두 계정이 공유하고 방은 로그인한 사용자의 친구 관계에서 찾는다. 날짜와 일과는 한국 시각으로 해석한다. API 시각에는 UTC 오프셋을 붙인다.

현재 채팅 통합본 `9d11a21`의 기반은 Django 세션, CSRF, 방별 메시지 기록과 예약 워커다. 기존 발송 영수증과 WebSocket 이벤트에 맞춰 아래 확장을 설계했다. [기존 메시지 API와 실행법](../chat-api.md)

## 1. 주간 편집과 승인

운영 API는 로그인과 발행 관리 권한을 모두 검사한다. 사용자 API와 운영 API의 쓰기 요청에는 JSON 본문과 `X-CSRFToken`을 보낸다. 아래 경로는 `/api/ops/` 아래에 있다. 주차 `revision`은 운영자가 본 편집본을 식별하고, `version`은 후보 하나의 내용·사진·일정 근거를 식별한다. 예약 작업은 주차 전체의 최신 번호가 아니라 자신이 승인받은 후보 버전을 검사한다.

| 동작 | 요청 예시 | 성공 응답 |
|---|---|---|
| 한 주 생성 | `POST weeks/generate/` · `{"week_start":"2027-04-19"}` | `202 {"id":12,"generation":"pending"}`. 주차 시작일은 월요일이다. 같은 주 요청은 진행 중이면 같은 ID와 202, 이미 생성됐으면 기존 ID와 200을 돌려준다. |
| 주간 조회 | `GET weeks/12/` | `200`. 시작일, generation, revision과 두 캐릭터의 후보 목록. 후보에는 post_id, character_id, version, state, calendar_ids, public_facts, operator_note, caption, photo_id, photo_alt, publish_at, issues가 있다. |
| 수정 | `PATCH weeks/12/posts/501/` · `{"expected_revision":1,"caption":"주황 쪽으로 마음 굳힘 🍊"}` | `200 {"revision":2,"post_id":501,"version":2,"state":"draft"}`. 이 행은 본 흐름과 독립된 수정 예시다. |
| 제외 | `PATCH weeks/12/posts/503/` · `{"expected_revision":1,"state":"skipped"}` | `200 {"revision":2,"post_id":503,"version":2,"state":"skipped"}`. 이미 발행된 게시물은 주간 편집 대상에서 제외한다. |
| 묶음 승인 | `POST weeks/12/approve/` · `{"expected_revision":2}` | `200 {"revision":3,"scheduled":[{"post_id":501,"version":1},{"post_id":502,"version":1}]}`. 제외한 503은 예약 목록에 들어가지 않는다. |
| 실패 건 재시도 | `POST weeks/12/posts/501/retry/` · `{"expected_revision":4,"version":1}` | `202 {"post_id":501,"version":1,"state":"scheduled"}`. 실패 분기에서 운영자가 마지막으로 조회한 편집본이 4인 예다. 같은 승인 버전의 예약을 다시 실행한다. |

샘플의 `week_revision`과 `expected_week_revision`은 HTTP의 `revision`과 `expected_revision`에 해당한다. 생성 어댑터는 모델 후보의 `asset_key`를 운영 자산 목록과 대조해 `photo_id`로 기록하고 `public_fact`를 `public_facts` 목록에 보존한다. `editor_note`는 운영자만 읽는 `operator_note`가 된다. 후보의 `post_id`는 발행 뒤 피드의 `id`로 사용한다. 인용 요청은 이를 `source_post_id`로 전달한다.

생성 작업이 끝나면 주간 조회의 `generation`은 `ready`, 첫 편집본 번호는 1이 된다. 주간 조회는 대기 중에도 가능하며, 생성 실패 시 `generation:"failed"`와 오류 사유를 제공한다. 이 상태에서 같은 생성 요청을 보내면 기존 주차에서 재시도한다. 완성된 후보 묶음만 한 트랜잭션으로 저장한다. 생성 중에 달력이 바뀌면 새 입력으로 다시 생성한다. 운영자는 첫 베타에서 Django admin으로 가상 학교 달력을 편집하며 운영 API와 같은 일정 변경 처리를 호출한다.

편집·제외·승인은 주차를 잠근 뒤 `expected_revision`을 비교한다. 두 사람이 편집본 2를 열고 한 사람이 승인한 뒤 다른 사람이 수정하면 `409 {"code":"week_changed","detail":"주간 계획이 바뀌었어요.","current_revision":3}`를 반환한다. 화면은 최신 계획을 다시 불러와 수정 내용을 확인시킨다. 예약 실패·재시도 결과는 후보의 실행 상태로 기록하며 내용에 대한 주차 편집 번호를 올리지 않는다. 재시도 때 이미 같은 버전이 예약됐으면 202, 발행됐으면 200으로 현재 결과를 돌려준다.

승인은 포함된 초안의 날짜·일과 충돌, 같은 사건의 중복, 선택한 사진과 설명, 공개 가능한 시각을 검사한다. 보류된 항목이 포함되면 전체를 그대로 두고 `422 {"code":"review_required","posts":[{"post_id":503,"issues":["missing_photo"]}]}`를 반환한다. 제외하거나 수정한 뒤 다시 승인한다. 승인, 후보 버전 고정, 예약 생성은 함께 커밋한다. 체육대회 연기는 해당 일정에 의존하는 미발행 후보 502만 새 버전의 `draft`로 되돌리고 주차 번호를 4로 올린다. 예약 실행 직전에도 해당 후보의 승인 버전을 다시 검사한다. 이렇게 해야 501은 처음 약속한 시각에 공개되고, 옛 502 작업은 늦게 실행돼도 공개되지 않는다.

## 2. 피드·프로필·사진

| 요청 | 공개 범위와 응답 |
|---|---|
| `GET /api/feed/` | 본인의 친구가 실제 발행한 글을 실제 공개 시각과 ID의 내림차순으로 반환한다. 첫 페이지 30건, 다음 페이지는 응답의 불투명한 `next_cursor`를 `?cursor=`로 전달한다. 마지막 페이지는 null이다. |
| `GET /api/characters/2/profile/` | 친구의 `id, name, profile_image, status_message, current_activity, room_id, is_ai`와, 피드와 동일한 형태의 `posts`와 `next_cursor`를 돌려준다. 사진 탭의 다음 페이지도 같은 프로필 경로에 cursor를 전달한다. `status_message`는 비어 있어도 된다. `current_activity`는 서버 시계에서 구한다. A 응답의 `room_id`는 31, B 응답은 32다. |
| `GET /api/posts/501/photo/` | 인증·친구 관계·현재 공개 버전을 확인한 뒤 이미지 바이너리를 전달한다. 이 경로를 썸네일과 확대 화면에 함께 쓴다. 원본 저장소는 비공개이며 응답은 `Cache-Control: private, no-store`를 쓴다. |

다음은 4월 21일 16:30에 발행된 포스터가 한 건 있는 피드 응답 예시다. `published_at`은 실제 공개 트랜잭션의 시각이다. 16:30에 실패하고 16:32에 재시도한 분기에서는 16:32로 반환한다.

```json
{
  "posts": [{
    "id": 501,
    "version": 1,
    "character_id": 2,
    "published_at": "2027-04-21T16:30:00+09:00",
    "caption": "둘 다 뽑아 보길 잘했다. 오늘은 주황 🍊",
    "photo": {
      "url": "/api/posts/501/photo/",
      "alt": "작업실 책상의 군청색과 주황색 포스터 시안",
      "ai_generated": true
    }
  }],
  "next_cursor": null
}
```

예약 시각이 지나도 발행 트랜잭션이 성공해야 피드와 프로필에서 볼 수 있다. 실패하면 직전 공개 목록을 읽는다. 사진의 직접 요청에서도 미발행·없는 게시물·접근 권한이 없는 게시물은 동일한 404를 반환한다. 사용자 응답에 초안, 프롬프트, 원본 자산 키, 내부 일기나 다른 방의 정보를 넣지 않는다.

## 3. 사진에서 기존 방으로

1. A가 사진의 ‘서윤에게 말 걸기’를 누르면 `GET /api/friends/`의 `character.id=2`에 해당하는 방 31을 연다. B는 같은 사진에서 방 32로 간다. 작성창에 사진 인용을 놓고, 사용자가 전송을 누를 때 메시지를 만든다.
2. 기존 `POST /api/rooms/31/messages/` 본문에 선택적 정수 `source_post_id`를 받는다. 예전 `{"text":"뭐해?"}` 형식과 공백·1,000자 검사는 그대로 적용한다. 응답은 기존 영수증이다.

```json
{"text":"그래서 결국 무슨 색 골랐어?","source_post_id":501}
```

```json
{"id":911,"status":"sent","created_at":"2027-04-21T16:31:00+09:00"}
```

3. 방 소유자를 확인한 뒤, 인용 글의 캐릭터가 방과 같고 요청 시점에 공개됐는지 검사한다. 접근 불가와 다른 캐릭터의 글에는 같은 404와 `{"detail":"이 게시물을 인용할 수 없어요."}`를 반환한다. 잘못된 ID 타입은 400, 로그인 필요는 401, 다른 사용자의 방은 기존 403이다. 거절된 요청으로 메시지나 답장 계획을 만들지 않는다. 화면은 작성문을 보존하고 인용을 해제할 수 있게 한다.
4. 승인된 요청은 기존 방 잠금 아래 메시지와 인용한 공개 버전을 함께 저장한다. 후속 전송은 기존 계획의 최신 메시지를 갱신한다. 첫 전송의 읽음·답장 기한을 유지하고 같은 대기 묶음에서 사용자가 참조한 글을 문맥에 포함한다. 기록 GET의 메시지 및 WS의 `message.new.message`는 같은 추가 필드 `source_post`를 쓴다. 일반 메시지는 null, 유효한 인용은 `{"id":501,"version":1,"status":"available","caption":"둘 다 뽑아 보길 잘했다. 오늘은 주황 🍊","photo_url":"/api/posts/501/photo/"}`이다. 조회 시 회수된 인용은 `{"status":"unavailable"}`로 내려 ‘공개가 종료된 사진’으로 표시한다.
5. 채팅 헤더의 이름·얼굴 또는 서윤 말풍선 옆 얼굴을 누르면 같은 프로필을 연다. 프로필을 여는 동안 방 ID, 입력문·인용, 보고 있던 메시지 ID와 화면 내 위치를 보존한다. 닫기와 브라우저 뒤로가기는 그 위치로 복귀한다. 읽던 중 새 답장이 왔으면 새 메시지로 이동하는 동작을 제공한다. 계정 전환과 로그아웃 시 사용자별 임시 상태를 비운다.

### 예약 워커와 이벤트

통합본의 `/ws/chat/31/`은 세션 소유자에게 방 이벤트를 보낸다. 다음은 수신 순서 예시이며 시각은 서버 일과가 계산한다. 입력 중에 새로 접속했거나 접속이 끊겼다면 REST 기록을 다시 읽고 메시지 ID로 합친다. 친구 목록의 마지막 메시지와 방 기록, 새 메시지 이벤트는 동일한 공개 조건으로 인용을 직렬화한다. 공개 피드는 화면 진입·복귀 또는 새로고침 때 재조회한다.

```json
[
  {"type":"message.read","message_id":911,"read_at":"2027-04-21T16:31:00+09:00"},
  {"type":"typing.start","expires_at":"2027-04-21T16:31:30+09:00"},
  {"type":"typing.stop"},
  {"type":"message.new","message":{"id":912,"sender":"character","kind":"text","text":"주황!","status":"sent","created_at":"2027-04-21T16:31:30+09:00","read_at":null,"source_post":null}},
  {"type":"message.new","message":{"id":913,"sender":"character","kind":"text","text":"종이에 뽑으니까 훨씬 예쁘더라","status":"sent","created_at":"2027-04-21T16:31:30+09:00","read_at":null,"source_post":null}}
]
```

위 배열의 각 객체가 개별 WebSocket 프레임 하나다. 이미 발송 기한이 된 작업을 복구하면 읽음과 발송을 한 번에 처리하므로 `typing.start`가 생략될 수 있다. 발송 직전에 공개 버전·방·기억 동의를 재확인하고, 답장·계획 완료를 함께 기록한 후 커밋된 이벤트를 보낸다. 모델 오류로 재생성이 기한을 넘기면 그때 공개가 확인된 사실만 담은 검수 문구를 사용한다.
