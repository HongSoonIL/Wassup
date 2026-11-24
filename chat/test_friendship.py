from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connections
from django.test import Client, TestCase, TransactionTestCase

from accounts.models import User
from memory.models import RoomMemoryState
from world.models import Character
from .models import Friendship


class DiscoveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('new')
        cls.other = User.objects.create_user('other')
        cls.artist = Character.objects.create(
            name='서윤', age=22, major='디자인', status_message='과제 중',
            profile_image='characters/profile/artist.webp',
        )
        cls.barista = Character.objects.create(name='도현', age=23)
        cls.other_room = Friendship.objects.create(user=cls.other, character=cls.artist)

    def test_guests_need_a_session_for_discovery_and_friend_creation(self):
        for response in [
            self.client.get('/api/characters/'),
            self.client.post('/api/friends/', {'character_id': self.artist.pk}, content_type='application/json'),
        ]:
            self.assertEqual(response.status_code, 401)
            self.assertIn('detail', response.json())
        self.assertEqual(Friendship.objects.count(), 1)

    def test_discovery_shares_only_public_fields_and_my_room_id(self):
        self.client.force_login(self.user)
        room = Friendship.objects.create(user=self.user, character=self.barista)
        self.assertEqual(self.client.get('/api/characters/').json(), {'characters': [
            {'id': self.artist.pk, 'name': self.artist.name, 'status_message': self.artist.status_message,
             'profile_image': self.artist.profile_image.url, 'room_id': None},
            {'id': self.barista.pk, 'name': self.barista.name, 'status_message': '', 'profile_image': None,
             'room_id': room.pk},
        ]})
        self.assertEqual(self.client.get(f'/api/characters/{self.artist.pk}/profile/').status_code, 404)

    def test_add_friend_requires_csrf_and_reuses_the_room(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        data = {'character_id': self.artist.pk}
        self.assertEqual(csrf.post('/api/friends/', data, content_type='application/json').status_code, 403)
        self.assertFalse(self.user.friendships.exists())

        csrf.get('/discover/')
        for expected in (201, 200):
            response = csrf.post('/api/friends/', data, content_type='application/json',
                                 HTTP_X_CSRFTOKEN=csrf.cookies['csrftoken'].value)
            self.assertEqual(response.status_code, expected)
            room = self.user.friendships.get()
            self.assertEqual(response.json(), {'room_id': room.pk})
        self.assertNotEqual(room.pk, self.other_room.pk)
        self.assertEqual(csrf.get('/api/characters/').json()['characters'][0]['room_id'], room.pk)
        self.assertEqual(csrf.get('/api/friends/').json()['friends'][0]['room_id'], room.pk)
        self.assertEqual(csrf.get(f'/api/characters/{self.artist.pk}/profile/').json()['room_id'], room.pk)
        self.assertFalse(csrf.get(f'/api/rooms/{room.pk}/memories/').json()['enabled'])
        self.assertFalse(RoomMemoryState.objects.filter(room=room).exists())

    def test_add_friend_rejects_malformed_or_unknown_characters(self):
        self.client.force_login(self.user)
        for data in ['{', [], {}, {'character_id': True}, {'character_id': '1'}, {'character_id': 0},
                     {'character_id': 2**63}]:
            with self.subTest(data=data):
                response = self.client.post('/api/friends/', data, content_type='application/json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('detail', response.json())
        response = self.client.post('/api/friends/', {'character_id': self.barista.pk + 1},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.user.friendships.exists())


class FriendshipRaceTests(TransactionTestCase):
    def test_simultaneous_new_requests_get_the_same_room(self):
        user = User.objects.create_user('owner')
        friend = Character.objects.create(name='도현', age=23)
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(user)
        start = Barrier(2, timeout=5)

        def add(client):
            try:
                start.wait()
                response = client.post('/api/friends/', {'character_id': friend.pk}, content_type='application/json')
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(add, clients))
        room = Friendship.objects.get(user=user, character=friend)
        self.assertCountEqual(responses, [(201, {'room_id': room.pk}), (200, {'room_id': room.pk})])
