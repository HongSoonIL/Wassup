from django.urls import path

from . import views

app_name = 'chat'

urlpatterns = [
    path('characters/', views.character_list, name='character-list'),
    path('rooms/<int:room_id>/read/', views.read_received, name='room-read'),
    path('friends/', views.friend_list, name='friend-list'),
    path('friends/<int:room_id>/', views.friend_detail, name='friend-detail'),
    path('rooms/<int:room_id>/messages/', views.room_messages, name='room-messages'),
    path('rooms/<int:room_id>/messages/<int:message_id>/photo/', views.room_photo, name='room-photo'),
]
