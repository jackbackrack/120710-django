from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

from accounts.views import ArtistUserCreateView, CustomPasswordResetView, CustomSignupView, UserNameUpdateView, claim_artist, link_artist_to_user
from eatart.views.public import (index, visit, contact, about, howto, howto_guide,
                                 howto_reference, linktree, privacy)
from eatart.views.subscribe import subscribe, subscribe_kiosk
from eatart.views.unsubscribe import unsubscribe

#temporary hack as well as + static(...) below
from django.conf import settings
from django.conf.urls.static import static
# 

urlpatterns = [
    # Before everything else, and served straight from a template: /robots.txt used to 404,
    # which cost a full request cycle *and* left crawlers unguided. TemplateView means no
    # database query and no context processors.
    path('robots.txt', TemplateView.as_view(
        template_name='robots.txt', content_type='text/plain'), name='robots'),
    path('', index, name='index'),
    path('', include('gallery.urls')),
    path('', include('reviews.urls')),
    path('visit/', visit, name='visit'),
    path('contact/', contact, name='contact'),
    path('about/', about, name='about'),
    path('privacy/', privacy, name='privacy'),
    # Per-venue versions of the same four pages, served by the same views: the context
    # processor resolves the site from the path, so nothing here differs but the URL.
    path('site/<slug:site_slug>/visit/', visit, name='site_visit'),
    path('site/<slug:site_slug>/contact/', contact, name='site_contact'),
    path('site/<slug:site_slug>/about/', about, name='site_about'),
    path('site/<slug:site_slug>/privacy/', privacy, name='site_privacy'),
    path('site/<slug:site_slug>/links/', linktree, name='site_linktree'),
    path('howto/', howto, name='howto'),
    # Before the <slug:anchor> route, which would otherwise swallow "reference".
    path('howto/reference/', howto_reference, name='howto_reference'),
    path('howto/<slug:anchor>/', howto_guide, name='howto_guide'),
    path('links/', linktree, name='linktree'),
    path('subscribe/', subscribe, name='subscribe'),
    # One URL, two behaviours: a confirmation page on GET, one-click on POST for the
    # Unsubscribe button Gmail and Yahoo render. See the view.
    path('unsubscribe/<str:token>/', unsubscribe, name='unsubscribe'),
    # Resend delivery events. Anymail verifies the Svix signature before the
    # receiver in gallery/webhooks.py sees anything; the shared secret is
    # ANYMAIL['RESEND_SIGNING_SECRET'].
    path('anymail/', include('anymail.urls')),
    path('subscribe/kiosk/<str:token>/', subscribe_kiosk, name='subscribe_kiosk'),
    path('accounts/artist_user_new/', ArtistUserCreateView.as_view()),
    path('accounts/claim-artist/', claim_artist, name='claim_artist'),
    path('accounts/link-artists/', link_artist_to_user, name='link_artist_to_user'),
    path('accounts/profile/', UserNameUpdateView.as_view(), name='account_profile'),
    path("admin/", admin.site.urls),
    path("accounts/signup/", CustomSignupView.as_view()),
    path("accounts/password/reset/", CustomPasswordResetView.as_view()),
    path("accounts/", include("allauth.urls")),
]

# Only serve media files via Django in local/debug filesystem mode.
if settings.DEBUG and hasattr(settings, "MEDIA_ROOT"):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
