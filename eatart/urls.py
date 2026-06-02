from django.contrib import admin
from django.urls import path, include

from accounts.views import ArtistUserCreateView, CustomPasswordResetView, CustomSignupView, UserNameUpdateView
from eatart.views.public import index, visit, contact, about, howto
from eatart.views.subscribe import subscribe, subscribe_kiosk

#temporary hack as well as + static(...) below
from django.conf import settings
from django.conf.urls.static import static
# 

urlpatterns = [
    path('', index, name='index'),
    path('', include('gallery.urls')),
    path('', include('reviews.urls')),
    path('visit/', visit, name='visit'),
    path('contact/', contact, name='contact'),
    path('about/', about, name='about'),
    path('howto/', howto, name='howto'),
    path('subscribe/', subscribe, name='subscribe'),
    path('subscribe/kiosk/', subscribe_kiosk, name='subscribe_kiosk'),
    path('accounts/artist_user_new/', ArtistUserCreateView.as_view()),
    path('accounts/profile/', UserNameUpdateView.as_view(), name='account_profile'),
    path("admin/", admin.site.urls),
    path("accounts/signup/", CustomSignupView.as_view()),
    path("accounts/password/reset/", CustomPasswordResetView.as_view()),
    path("accounts/", include("allauth.urls")),
]

# Only serve media files via Django in local/debug filesystem mode.
if settings.DEBUG and hasattr(settings, "MEDIA_ROOT"):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
