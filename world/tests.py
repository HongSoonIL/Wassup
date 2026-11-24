from datetime import time
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase

from .models import Character, Persona, RoutineTemplate, State


class SeedWorldTests(TestCase):
    def test_seed_is_idempotent(self):
        call_command('seed_world', stdout=StringIO())
        call_command('seed_world', stdout=StringIO())

        self.assertEqual(Character.objects.count(), 2)
        self.assertEqual(Persona.objects.count(), 2)
        for character in Character.objects.all():
            self.assertEqual(character.routines.order_by().values('weekday').distinct().count(), 7)
        dohyun = Character.objects.get(name='도현')
        self.assertEqual(dohyun.persona.ignore_percent, 15)
        self.assertEqual(dohyun.routines.filter(weekday=RoutineTemplate.Weekday.THU).count(), 11)
        self.assertTrue(
            dohyun.routines.filter(activity='카페 알바', start_time=time(18), end_time=time(22), state=State.FOCUS).exists()
        )

    def test_edited_weekday_survives_reseeding(self):
        call_command('seed_world', stdout=StringIO())
        artist = Character.objects.get(name='서윤')
        studio = artist.routines.get(weekday=1, start_time=time(14))
        studio.activity = '전시 설치'
        studio.save(update_fields=['activity'])
        call_command('seed_world', stdout=StringIO())
        studio.refresh_from_db()
        self.assertEqual(studio.activity, '전시 설치')


class RoutineTemplateRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.character = Character.objects.create(name='도현', age=23, major='경영학과 3학년')

    def routine(self, start, end):
        return RoutineTemplate(
            character=self.character, weekday=RoutineTemplate.Weekday.THU,
            start_time=start, end_time=end, activity='집', state=State.FREE,
        )

    def test_midnight_end_is_allowed(self):
        r = self.routine(time(22, 30), time(0, 0))
        r.full_clean()
        r.save()

    def test_end_before_start_is_rejected(self):
        r = self.routine(time(22, 30), time(1, 30))
        with self.assertRaises(ValidationError):
            r.full_clean()
        with self.assertRaises(IntegrityError):
            r.save()


class PersonaRuleTests(TestCase):
    def test_bubble_max_below_min_is_rejected(self):
        character = Character.objects.create(name='서윤', age=22, major='시각디자인과 3학년')
        persona = Persona(character=character, personality='공감파', speech_rules='길게', bubble_min=3, bubble_max=2)
        with self.assertRaises(ValidationError):
            persona.full_clean()
        with self.assertRaises(IntegrityError):
            persona.save()
