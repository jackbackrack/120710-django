from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from accounts.signup import apply_google_profile_data, ensure_signup_profile

class NoNewUsersAccountAdapter(DefaultAccountAdapter):

    def is_open_for_signup(self, request):
        return True


class SocialAccountAdapter(DefaultSocialAccountAdapter):

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
