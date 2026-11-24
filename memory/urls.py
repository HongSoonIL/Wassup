from django.urls import path

from . import views

app_name = 'memory'

urlpatterns = [
    path('rooms/<int:room_id>/memories/', views.room_memories, name='room-memories'),
    path('rooms/<int:room_id>/memories/<int:memory_id>/', views.room_memory, name='room-memory'),
]
