import json
from unicodedata import category
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from chat.views import error, json_response, login_required_json
from dashboard.feed import personal_post_json, post_page
from world.publication import ReviewError

from .models import PersonalPost, User
from .private_photos import normalize_photo, private_photo_response


def create_post(request):
    client_id = request.headers.get('Idempotency-Key')
    if client_id is not None:
        try:
            client_id = UUID(client_id)
        except ValueError:
            return error(400, '게시물 전송 번호를 확인해 주세요.')

    photo = None
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
        except (ValueError, RecursionError):
            return error(400, 'JSON 객체를 보내 주세요.')
        if not isinstance(data, dict) or data.keys() - {'caption', 'photo'} or data.get('photo') is not None:
            return error(400, '글을 입력하고 사진은 파일로 올려 주세요.')
    elif request.content_type == 'multipart/form-data':
        data = request.POST
        uploads = request.FILES.getlist('photo')
        if (data.keys() - {'caption', 'csrfmiddlewaretoken'} or request.FILES.keys() - {'photo'}
                or len(uploads) > 1 or len(data.getlist('caption')) > 1):
            return error(400, '글 한 개와 사진 한 장까지 올릴 수 있어요.')
        if uploads:
            photo = uploads[0]
    else:
        return error(415, 'JSON 또는 multipart/form-data로 보내 주세요.')

    caption = data.get('caption', '')
    if (not isinstance(caption, str) or len(caption) > 4000 or '\x00' in caption
            or any(category(char) == 'Cs' for char in caption)):
        return error(400, '글은 4,000자까지 적을 수 있어요. 입력한 내용을 확인해 주세요.')
    caption = caption.strip()
    if not caption and photo is None:
        return error(400, '글을 적거나 사진을 올려 주세요.')
    post = PersonalPost(owner=request.user, caption=caption, client_post_id=client_id)
    if photo is not None:
        try:
            post.photo = normalize_photo(photo)
        except ValidationError as exc:
            return error(400, exc.messages[0])
    try:
        with transaction.atomic():
            if client_id is not None:
                # A retry may be in another transaction with its file saved and its row still invisible.
                User.objects.select_for_update(no_key=True).only('pk').get(pk=request.user.pk)
                existing = request.user.personal_posts.filter(client_post_id=client_id).first()
                if existing:
                    return json_response({'post': personal_post_json(existing)}, status=201)
            post.save()
    except Exception:
        # ImageField writes the new file before the INSERT, outside the database transaction.
        if post.photo and post.photo._committed and not PersonalPost.objects.filter(photo=post.photo.name).exists():
            post.photo.storage.delete(post.photo.name)
        raise
    return json_response({'post': personal_post_json(post)}, status=201)


@require_http_methods(['GET', 'POST'])
@never_cache
@ensure_csrf_cookie
@login_required_json
def my_posts(request):
    if request.method == 'POST':
        return create_post(request)
    try:
        return json_response(post_page(request.GET.get('cursor'), personal=request.user.personal_posts.all()))
    except ReviewError as exc:
        return json_response(exc.body, exc.status)


@require_http_methods(['DELETE'])
@never_cache
@login_required_json
@transaction.atomic
def delete_post(request, post_id):
    post = get_object_or_404(request.user.personal_posts.select_for_update(), pk=post_id)
    post.delete()
    return HttpResponse(status=204)


@require_GET
@never_cache
@login_required_json
def post_photo(request, post_id):
    post = get_object_or_404(request.user.personal_posts.only('photo'), pk=post_id)
    return private_photo_response(post.photo)
