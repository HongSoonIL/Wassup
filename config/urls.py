"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from chat.views import chat_page, from_post
from config.health import ready

urlpatterns = [
    path("health/ready/", ready),
    path('', include('accounts.urls')),
    path('', include('dashboard.urls')),
    path('admin/', admin.site.urls),
    path('api/', include('chat.urls')),
    path('api/', include('memory.urls')),
    path('chat/from-post/<int:post_id>/', from_post),
    path('chat/<int:room_id>/', chat_page, name='chat-page'),
]

# 개발 중에만 Django가 업로드 파일(프사 등)을 직접 내준다
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
