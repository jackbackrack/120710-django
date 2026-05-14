import logging

from django.core.exceptions import MultipleObjectsReturned

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from accounts.signup import apply_google_profile_data, ensure_signup_profile


logger = logging.getLogger(__name__)

class NoNewUsersAccountAdapter(DefaultAccountAdapter):

    def is_open_for_signup(self, request):
        return True


class SocialAccountAdapter(DefaultSocialAccountAdapter):

    def get_app(self, request, provider, client_id=None):
        try:
            return super().get_app(request, provider, client_id=client_id)
        except MultipleObjectsReturned:
            apps = self.list_apps(request, provider=provider, client_id=client_id)
            visible_apps = [app for app in apps if not app.settings.get('hidden')]
            fallback_apps = visible_apps or apps
            if not fallback_apps:
                raise

            app = fallback_apps[0]
            logger.warning(
                'Multiple social apps matched provider=%s client_id=%s; '
                'using app id=%s name=%s as fallback.',
                provider,
                client_id,
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
        ensure_signup_profile(user)
        return user
