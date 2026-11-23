from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ('wassup', {'fields': ('nickname', 'birth_year', 'agreed_at', 'allow_proactive', 'usage_time_alert')}),
    )
    list_display = ('username', 'nickname', 'birth_year', 'is_staff', 'date_joined')
