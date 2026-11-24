from django.contrib.auth.views import LogoutView
from django.urls import path

from . import post_views, views

urlpatterns = [
    path('', views.entry, name='entry'),
    path('login/', views.SessionLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='/login/'), name='logout'),
    path('signup/', views.signup, name='signup'),
    path('home/', views.friends_page, {'page': 'home'}, name='home'),
    path('messages/', views.friends_page, {'page': 'messages'}, name='messages'),
    path('me/', views.my_profile, name='my-profile'),
    path('api/me/preferences/', views.my_preferences, name='my-preferences'),
    path('api/me/posts/', post_views.my_posts, name='my-posts'),
    path('api/me/posts/<int:post_id>/', post_views.delete_post, name='my-post'),
    path('api/me/posts/<int:post_id>/photo/', post_views.post_photo, name='my-post-photo'),
    path('discover/', views.friends_page, {'page': 'discover'}, name='discover'),
    path('people/<int:character_id>/', views.friends_page, {'page': 'profile'}, name='friend-profile'),
]
