from django.conf import settings
from django.db import DatabaseError
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from redis import Redis, RedisError
from accounts.models import SignupInvite
from world.models import Character


@require_safe
@never_cache
def ready(request):
    try:
        SignupInvite.objects.exists()
        if settings.TEAM_DEPLOYMENT and not Character.objects.exists():
            return HttpResponse(status=503)
        with Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2) as client:
            client.ping()
    except (DatabaseError, RedisError):
        return HttpResponse(status=503)
    return HttpResponse('ready', content_type='text/plain')
