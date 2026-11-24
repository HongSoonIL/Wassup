from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path('', views.entry, name='entry'),
    path('login/', views.SessionLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='/login/'), name='logout'),
    path('signup/', views.signup, name='signup'),
    path('home/', views.friends_page, {'page': 'home'}, name='home'),
    path('discover/', views.friends_page, {'page': 'discover'}, name='discover'),
    path('people/<int:character_id>/', views.friends_page, {'page': 'profile'}, name='friend-profile'),
]
