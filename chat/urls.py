from django.urls import path

from . import views

app_name = 'chat'

urlpatterns = [
    path('friends/', views.friend_list, name='friend-list'),
    path('rooms/<int:room_id>/messages/', views.room_messages, name='room-messages'),
]
