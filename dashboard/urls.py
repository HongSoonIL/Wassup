from django.urls import path

from . import views

urlpatterns = [
    path('studio/', views.studio_page),
    path('feed/', views.feed_page),
    path('api/ops/weeks/generate/', views.generate),
    path('api/ops/weeks/<int:week_id>/', views.week_detail),
    path('api/ops/weeks/<int:week_id>/approve/', views.approve_week),
    path('api/ops/weeks/<int:week_id>/posts/<int:post_id>/', views.patch_post),
    path('api/ops/weeks/<int:week_id>/posts/<int:post_id>/regenerate-photo/', views.regenerate_photo),
    path('api/ops/weeks/<int:week_id>/posts/<int:post_id>/retry/', views.retry_post),
    path('api/ops/photos/<slug:key>/', views.preview),
    path('api/feed/', views.feed),
    path('api/characters/<int:character_id>/profile/', views.profile),
    path('api/posts/<int:post_id>/photo/', views.photo),
]
