from django.contrib import admin

from .models import Artist, Piece, Show

admin.site.register(Piece)
admin.site.register(Artist)
admin.site.register(Show)
