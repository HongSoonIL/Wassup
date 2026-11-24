from django.contrib import admin

from .models import Friendship, Message, ReplyPlan


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'character', 'created_at')
    list_filter = ('character',)
    search_fields = ('user__username', 'user__nickname')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'room', 'sender', 'kind', 'text', 'created_at', 'read_at')
    list_filter = ('sender', 'kind')
    list_select_related = ('room__user', 'room__character')
    search_fields = ('text',)


@admin.register(ReplyPlan)
class ReplyPlanAdmin(admin.ModelAdmin):
    list_display = ('id', 'room', 'status', 'read_at', 'typing_at', 'reply_at', 'model_name', 'generation_ms', 'attempts')
    list_filter = ('status',)
    list_select_related = ('room__user', 'room__character')
    raw_id_fields = ('trigger_message',)
