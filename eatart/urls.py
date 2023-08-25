from django.contrib import admin
from django.urls import path, include

from market.views import index, contact

#temporary hack as well as + static(...) below
from django.conf import settings
from django.conf.urls.static import static
# 

urlpatterns = [
    path('', index, name='index'),
    #path('pieces/', include('piece.piece_urls')),
    path('pieces/pieces/', include('piece.urls')),
    path('pieces/shows/', include('piece.urls')),
    path('pieces/artists/', include('piece.urls')),
    path('contact/', contact, name='contact'),
    path('admin/', admin.site.urls),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
