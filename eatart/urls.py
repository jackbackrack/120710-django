from django.contrib import admin
from django.urls import path, include

from market.views import index, visit, contact, about, howto, subscribe, subscribe_success, subscribe_failure
from accounts.views import CustomSignupView, CustomPasswordResetView

#temporary hack as well as + static(...) below
from django.conf import settings
from django.conf.urls.static import static
# 

urlpatterns = [
    path('', index, name='index'),
    path('', include('piece.urls')),
    path('visit/', visit, name='visit'),
    path('contact/', contact, name='contact'),
    path('about/', about, name='about'),
    path('howto/', howto, name='howto'),
    path('subscribe/', subscribe, name='subscribe'),
    path("subscribe_success/", subscribe_success, name="subscribe_success"),
    path("subscribe_failure/", subscribe_failure, name="subscribe_failure"),
    path("admin/", admin.site.urls),
    path("accounts/signup/", CustomSignupView.as_view()),
    path("accounts/password/reset/", CustomPasswordResetView.as_view()),
    path("accounts/", include("allauth.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
