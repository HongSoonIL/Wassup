from urllib.parse import urlsplit

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods

from .forms import SignupForm
from .models import User


def safe_next(request):
    target = request.POST.get('next', request.GET.get('next', '')).strip()
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        scheme = urlsplit(target).scheme
        if not scheme or scheme == request.scheme:
            return target
    return ''


class SessionLoginView(LoginView):
    template_name = 'dashboard/login.html'
    next_page = 'feed'

    def get_redirect_url(self):
        return safe_next(self.request)


@require_GET
def entry(request):
    return redirect('feed')


@sensitive_post_parameters('password1', 'password2', 'birth_date')
@never_cache
@require_http_methods(['GET', 'POST'])
def signup(request):
    target = safe_next(request)
    destination = target or reverse('feed')
    if request.user.is_authenticated:
        return HttpResponseRedirect(destination)
    form = SignupForm(request.POST if request.method == 'POST' else None)
    if form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            if not User.objects.filter(username=form.cleaned_data['username']).exists():
                raise
            form.add_error('username', form.instance.unique_error_message(User, ['username']))
        else:
            login(request, user)
            return HttpResponseRedirect(destination)
    return render(request, 'accounts/signup.html', {'form': form, 'next': target})


@require_GET
@login_required
@ensure_csrf_cookie
def friends_page(request, page, character_id=None):
    if character_id is not None:
        get_object_or_404(request.user.friendships.only('pk'), character_id=character_id)
    return render(request, 'accounts/friends.html', {'page': page, 'character_id': character_id})
