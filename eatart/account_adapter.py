import logging

from django.core.exceptions import MultipleObjectsReturned

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from accounts.signup import apply_google_profile_data, ensure_signup_profile


logger = logging.getLogger(__name__)

class NoNewUsersAccountAdapter(DefaultAccountAdapter):

    def is_open_for_signup(self, request):
        return True

    def login(self, request, user):
        result = super().login(request, user)
        from accounts.signup import _link_invitations
        artist = user.artists.first()
        _link_invitations(user, artist)
        return result

    def get_signup_redirect_url(self, request):
        from django.urls import reverse
        from django.contrib import messages
        claimed_pk = request.session.pop('claimed_artist_pk', None)
        if claimed_pk:
            from gallery.models import Artist
            try:
                artist = Artist.objects.get(pk=claimed_pk)
                messages.success(
                    request,
                    f'Welcome! Your account has been linked to your existing artist profile '
                    f'"{artist.name}" because your email matched our records.',
                )
                return reverse('gallery:artist_edit', kwargs={'pk': claimed_pk})
            except Artist.DoesNotExist:
                pass
        new_pk = request.session.pop('new_artist_pk', None)
        if new_pk:
            return reverse('gallery:artist_edit', kwargs={'pk': new_pk})
        artist = request.user.artists.order_by('-created_at').first()
        if artist:
            return reverse('gallery:artist_edit', kwargs={'pk': artist.pk})
        return super().get_signup_redirect_url(request)

    def get_login_redirect_url(self, request):
        from django.urls import reverse
        new_pk = request.session.pop('new_artist_pk', None)
        if new_pk:
            return reverse('gallery:artist_edit', kwargs={'pk': new_pk})
        artist = request.user.artists.order_by('-created_at').first()
        if artist:
            return artist.get_absolute_url()
        return super().get_login_redirect_url(request)


class SocialAccountAdapter(DefaultSocialAccountAdapter):

    def get_app(self, request, provider, client_id=None):
        apps = list(self.list_apps(request, provider=provider, client_id=client_id))

        if not apps:
            return super().get_app(request, provider, client_id=client_id)

        visible_apps = [app for app in apps if not app.settings.get('hidden')]
        candidate_apps = visible_apps or apps

        if client_id:
            client_matched = [
                app for app in candidate_apps
                if getattr(app, 'client_id', None) == client_id
            ]
            if client_matched:
                candidate_apps = client_matched

        # Pick deterministically so duplicate records do not randomly change behavior.
        app = sorted(
            candidate_apps,
            key=lambda candidate: (
                getattr(candidate, 'id', None) is None,
                getattr(candidate, 'id', 0) or 0,
                getattr(candidate, 'name', '') or '',
            ),
        )[0]

        if len(candidate_apps) > 1:
            app_ids = [getattr(candidate, 'id', None) for candidate in candidate_apps]
            logger.warning(
                'Multiple social apps matched provider=%s client_id=%s ids=%s; '
                'using app id=%s name=%s as fallback.',
                provider,
                client_id,
                app_ids,
                getattr(app, 'id', None),
                getattr(app, 'name', None),
            )

        return app

    def is_open_for_signup(self, request, sociallogin):
        """Allow social (Google) signups."""
        return True

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra_data = getattr(getattr(sociallogin, 'account', None), 'extra_data', {})
        apply_google_profile_data(user, extra_data)
        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        extra_data = getattr(getattr(sociallogin, 'account', None), 'extra_data', {})
        changed_fields = apply_google_profile_data(user, extra_data)
        if changed_fields:
            user.save(update_fields=changed_fields)
        artist, status = ensure_signup_profile(user)
        if artist and status == 'claimed':
            request.session['claimed_artist_pk'] = artist.pk
        elif artist and status == 'created':
            request.session['new_artist_pk'] = artist.pk
        return user
