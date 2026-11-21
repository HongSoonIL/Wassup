from django.contrib import admin

from .models import Character, Persona, RoutineTemplate


class PersonaInline(admin.StackedInline):
    model = Persona
    can_delete = False


class RoutineTemplateInline(admin.TabularInline):
    model = RoutineTemplate
    extra = 1


@admin.register(Character)
class CharacterAdmin(admin.ModelAdmin):
    list_display = ('name', 'age', 'major', 'status_message')
    search_fields = ('name',)
    inlines = [PersonaInline, RoutineTemplateInline]


@admin.register(RoutineTemplate)
class RoutineTemplateAdmin(admin.ModelAdmin):
    list_display = ('character', 'weekday', 'start_time', 'end_time', 'activity', 'state')
    list_filter = ('character', 'weekday', 'state')
