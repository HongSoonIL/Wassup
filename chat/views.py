"""채팅 REST API (설계서 3.6절, 부록 B).

화면용 API라 세션 로그인과 CSRF를 그대로 쓴다.
- 로그인 안 함 → 401
- 남의 채팅방 → 403
- 없는 채팅방 → 404
메시지 보내기의 응답에는 답장이 담기지 않는다. 답장은 2단계부터 실시간 이벤트로 온다.

CSRF: GET 응답이 csrftoken 쿠키를 심어 준다. 화면 JS는 POST할 때 그 값을
X-CSRFToken 헤더에 담아 보내야 한다. 없으면 Django가 403을 돌려준다.
"""
import json
from functools import wraps

from django.conf import settings
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import Friendship, Message, ReplyBubble, ReplyPlan
from world.clock import routine_at, world_now
from world.publication import published_posts
from world.models import FeedPost
from .replies import accept_message
from .serialization import iso, message_json, quote_json

MESSAGE_MAX_LENGTH = 1000
PAGE_SIZE = 30
PAGE_SIZE_MAX = 100


def json_response(data, status=200):
    # 한글을 \uXXXX로 바꾸지 않고 그대로 내보낸다 (브라우저·curl에서 바로 읽힌다)
    return JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})


def error(status, detail):
    return json_response({'detail': detail}, status=status)


def login_required_json(view):
    """로그인 페이지로 보내는 대신 401 JSON을 돌려준다."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error(401, '로그인이 필요해요.')
        return view(request, *args, **kwargs)

    return wrapper


def get_own_room(request, room_id):
    """없으면 404, 남의 방이면 None을 돌려준다 (호출한 쪽에서 403)."""
    room = get_object_or_404(Friendship, pk=room_id)
    return room if room.user_id == request.user.id else None


@require_GET
@login_required_json
@ensure_csrf_cookie
def chat_page(request, room_id):
    room = get_own_room(request, room_id)
    if room is None:
        return error(403, '내 채팅방이 아니에요.')
    source = request.GET.get('source_post')
    quote = None
    if source is not None:
        valid = source.isascii() and source.isdigit() and len(source) <= 19 and 0 < int(source) < 2**63
        post = published_posts(room.character_id, world_now()).filter(pk=int(source)).first() if valid else None
        quote = quote_json(post) if post else {'status': 'unavailable'}
    return render(request, 'chat/room.html', {
        'room_id': room.pk, 'initial_quote': quote,
        'sample_replies': settings.DEBUG and settings.LLM_BACKEND == 'fake',
    })


@require_GET
@login_required_json
def from_post(request, post_id):
    post = get_object_or_404(FeedPost, pk=post_id, state='published', published_at__lte=world_now())
    room = get_object_or_404(Friendship, user=request.user, character_id=post.character_id)
    return redirect(f'/chat/{room.pk}/?source_post={post.pk}')


@require_GET
@ensure_csrf_cookie
@login_required_json
def friend_list(request):
    """GET /api/friends/ — 친구 목록 (마지막 메시지, 안 읽은 수). 최근 대화한 방이 위로 온다."""
    last_message_id = Subquery(
        Message.objects.filter(room=OuterRef('pk')).order_by('-id').values('id')[:1]
    )
    rooms = list(
        Friendship.objects.filter(user=request.user)
        .select_related('character')
        .annotate(
            last_message_id=last_message_id,
            unread_count=Count(
                'messages',
                filter=Q(messages__sender=Message.Sender.CHARACTER, messages__read_at__isnull=True),
            ),
        )
        .order_by(F('last_message_id').desc(nulls_last=True), '-created_at')
    )
    last_messages = Message.objects.select_related('source_post', 'room').in_bulk([r.last_message_id for r in rooms if r.last_message_id])

    friends = []
    for room in rooms:
        c = room.character
        last = last_messages.get(room.last_message_id)
        friends.append({
            'room_id': room.id,
            'character': {
                'id': c.id,
                'name': c.name,
                'status_message': c.status_message,
                'profile_image': c.profile_image.url if c.profile_image else None,
            },
            'last_message': message_json(last) if last else None,
            'unread_count': room.unread_count,
        })
    return json_response({'friends': friends})


@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
@login_required_json
def room_messages(request, room_id):
    """GET: 이전 대화 불러오기 / POST: 메시지 보내기."""
    room = get_own_room(request, room_id)
    if room is None:
        return error(403, '내 채팅방이 아니에요.')
    if request.method == 'POST':
        return send_message(request, room)
    return list_messages(request, room)


def send_message(request, room):
    """POST /api/rooms/{id}/messages/ {"text": "..."} → 201."""
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return error(400, 'JSON 형식이 아니에요.')
    text = body.get('text') if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        return error(400, 'text를 입력해 주세요.')
    if len(text) > MESSAGE_MAX_LENGTH:
        return error(400, f'메시지는 {MESSAGE_MAX_LENGTH}자까지 보낼 수 있어요.')

    source_id = body.get('source_post_id')
    if 'source_post_id' in body and (type(source_id) is not int or not 0 < source_id < 2**63):
        return error(400, 'source_post_id는 양의 정수여야 해요.')
    try:
        message = accept_message(room.id, text, source_id)
    except FeedPost.DoesNotExist:
        return error(404, '이 게시물을 인용할 수 없어요.')
    return json_response(
        {'id': message.id, 'status': message.status, 'created_at': iso(message.created_at)},
        status=201,
    )


def list_messages(request, room):
    """GET /api/rooms/{id}/messages/?before=<메시지 id>&limit=30

    before보다 오래된 메시지를 최근 것부터 limit개 가져와 오래된 순으로 돌려준다.
    before가 없으면 가장 최근 메시지부터 가져온다.
    """
    try:
        before = int(request.GET['before']) if 'before' in request.GET else None
        limit = int(request.GET.get('limit', PAGE_SIZE))
    except ValueError:
        return error(400, 'before와 limit은 숫자여야 해요.')
    if limit < 1:
        return error(400, 'limit은 1 이상이어야 해요.')
    limit = min(limit, PAGE_SIZE_MAX)

    qs = room.messages.select_related('source_post', 'room').order_by('-id')
    if before is not None:
        qs = qs.filter(id__lt=before)
    page = list(qs[:limit + 1])  # 하나 더 가져와서 더 있는지 본다
    has_more = len(page) > limit
    page = page[:limit]
    page.reverse()
    data = {'messages': [message_json(m) for m in page], 'has_more': has_more}
    if before is None:
        now = world_now()
        expires = room.reply_plans.filter(status=ReplyPlan.Status.READY, typing_at__lte=now,
                                          reply_at__gt=now).values_list('reply_at', flat=True).first()
        if expires is None:
            expires = ReplyBubble.objects.filter(
                plan__room=room, status=ReplyBubble.Status.TYPING, typing_at__lte=now, deliver_at__gt=now,
            ).values_list('deliver_at', flat=True).first()
        slot = routine_at(room.character, now)
        data.update(typing_until=iso(expires, timespec='milliseconds'), current_activity=slot.activity if slot else None)
    return json_response(data)


@require_POST
@login_required_json
def read_received(request, room_id):
    room = get_own_room(request, room_id)
    if room is None:
        return error(403, '내 채팅방이 아니에요.')
    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return error(400, 'JSON 형식이 아니에요.')
    through = body.get('through_id') if isinstance(body, dict) else None
    if type(through) is not int or not 0 < through < 2**63:
        return error(400, '읽은 메시지를 확인해 주세요.')
    incoming = room.messages.filter(sender=Message.Sender.CHARACTER)
    if not incoming.filter(pk=through).exists():
        return error(404, '받은 메시지를 찾을 수 없어요.')
    incoming.filter(pk__lte=through, read_at__isnull=True).update(read_at=world_now())
    return json_response({'unread_count': incoming.filter(read_at__isnull=True).count()})
