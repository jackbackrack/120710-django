from django.contrib import admin
from django.urls import path, include

from market.views import index, visit, about, howto, signup, signup_success, signup_failure

#temporary hack as well as + static(...) below
from django.conf import settings
from django.conf.urls.static import static
# 

urlpatterns = [
    path('', index, name='index'),
    path('', include('piece.urls')),
    path('visit/', visit, name='visit'),
    path('about/', about, name='about'),
    path('howto/', howto, name='howto'),
    path('signup/', signup, name='signup'),
    path("signup_success/", signup_success, name="signup_success"),
    path("signup_failure/", signup_failure, name="signup_failure"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
