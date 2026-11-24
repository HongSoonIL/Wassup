from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from world.models import PublicationWeek


class StudioAccessTests(TestCase):
    def test_studio_and_direct_operator_endpoints_require_publish_permission(self):
        week = PublicationWeek.objects.create(start=date(2026, 11, 23))
        detail = f'/api/ops/weeks/{week.pk}/'
        self.assertRedirects(self.client.get('/studio/'), '/login/?next=/studio/')
        self.assertEqual(self.client.get(detail).status_code, 401)

        user = get_user_model().objects.create_user('reader')
        self.client.force_login(user)
        self.assertEqual(self.client.get('/studio/').status_code, 403)
        for url in (detail, '/api/ops/photos/example/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        for url in ('/api/ops/weeks/generate/', f'{detail}approve/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {}, content_type='application/json').status_code, 403)

        operator = get_user_model().objects.create_user('publisher')
        operator.user_permissions.add(Permission.objects.get(
            content_type__app_label='world', codename='publish_week',
        ))
        self.client.force_login(operator)
        self.assertContains(self.client.get('/studio/'), '<title>주간 발행 · wassup</title>')
        self.assertEqual(self.client.get(detail).json()['id'], week.pk)
        self.assertContains(self.client.get('/feed/'), '<title>피드 · wassup</title>')
        for url in ('/feed/', '/home/', '/messages/', '/discover/'):
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), 'data-can-publish')
