from django.urls import path

from . import views

app_name = 'piece'

urlpatterns = [
    path('pieces/pieces/<int:pk>/', views.piece_detail, name='piece_detail'),
    path('pieces/artists/<int:pk>/', views.artist_detail, name='artist_detail'),
    path('pieces/shows/<int:pk>/', views.show_detail, name='show_detail'),
]    
