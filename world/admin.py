from django.contrib import admin

from .models import Character, Persona, ProfilePhoto, RoutineTemplate, SchoolEvent


class PersonaInline(admin.StackedInline):
    model = Persona
    can_delete = False


class RoutineTemplateInline(admin.TabularInline):
    model = RoutineTemplate
    extra = 1


class ProfilePhotoInline(admin.TabularInline):
    """현재 프사를 바꾸면 자동으로 한 줄이 생긴다. 지난 프사를 직접 넣을 때는 시각을 과거로 적는다."""

    model = ProfilePhoto
    extra = 0
    fields = ('image', 'set_at', 'ai_generated')


@admin.register(Character)
class CharacterAdmin(admin.ModelAdmin):
    list_display = ('name', 'age', 'major', 'status_message')
    search_fields = ('name',)
    inlines = [PersonaInline, RoutineTemplateInline, ProfilePhotoInline]


@admin.register(RoutineTemplate)
class RoutineTemplateAdmin(admin.ModelAdmin):
    list_display = ('character', 'weekday', 'start_time', 'end_time', 'activity', 'state')
    list_filter = ('character', 'weekday', 'state')



@admin.register(SchoolEvent)
class SchoolEventAdmin(admin.ModelAdmin):
    list_display = ('label', 'kind', 'start_date', 'end_date_exclusive', 'revision')
