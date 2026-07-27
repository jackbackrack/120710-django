"""The single "what do I do next?" action for a viewer looking at a show.

One function, used wherever a show is displayed — the show page, the home page, the
show list, an artist's own page. Keeping it in one place is the point: the submission
flow only feels seamless if every entry into it says the same thing, and a card that
shows "Submit" to someone who cannot yet submit sends them into a dead end.
"""
from urllib.parse import urlencode

from django.urls import reverse

# Everything needed before submitting, named so a page can state the requirement up
# front instead of bouncing someone mid-submission. The photo is included: it is
# required, and saying so early is what keeps it from feeling like an ambush.
SUBMIT_REQUIRED = (('first_name', 'first name'),
                   ('last_name', 'last name'),
                   ('zipcode', 'zip code'),
                   ('image', 'photo'))


def artist_for(user):
    """The artist profile to submit as, or None."""
    if not user.is_authenticated:
        return None
    return user.artists.order_by('-created_at').first()


def submit_cta(request, show, artist=None, artist_loaded=False):
    """A dict describing the next action, or None if there is nothing to offer.

    Pass `artist` (with artist_loaded=True) when rendering a list of shows, so the
    profile is looked up once rather than per card.

    Returns {label, url, hint, step} and, for signed-out visitors, {alt_label,
    alt_url} for the sign-in alternative.
    """
    from gallery.models import ArtworkSubmission, Show

    if not show.is_accepting_submissions:
        return None

    submit_url = reverse('gallery:artwork_submit', kwargs={'slug': show.slug})
    user = request.user

    if not user.is_authenticated:
        # Two audiences arrive from the same announcement: people who need an account,
        # and people who already have one from a previous show. Offering only "Sign
        # up" left the returning half with no route in at all.
        return {'label': 'Sign up to submit',
                'url': f"{reverse('account_signup')}?{urlencode({'next': submit_url})}",
                'hint': 'Takes a minute — you will come straight back here.',
                'alt_label': 'Already have an account? Sign in',
                'alt_url': f"{reverse('account_login')}?{urlencode({'next': submit_url})}",
                'step': 1}

    if not artist_loaded:
        artist = artist_for(user)

    if artist is None:
        return {'label': 'Set up your artist profile',
                'url': f"{reverse('gallery:artist_new')}?{urlencode({'next': submit_url})}",
                'hint': 'Just a few details so we can credit your work.',
                'step': 2}

    if show.submission_type == Show.SUBMISSION_INVITED:
        from gallery.permissions import user_invited_to_show
        if not user_invited_to_show(show, user):
            return None

    missing = [label for field, label in SUBMIT_REQUIRED
               if not getattr(artist, field, None)]
    if missing:
        qs = urlencode({
            'highlight': ','.join(f for f, _l in SUBMIT_REQUIRED
                                  if not getattr(artist, f, None)),
            'next': submit_url})
        if missing == ['photo']:
            hint = ('Just a photo of you left — it prints in the show catalogue. '
                    'A phone snapshot is fine.')
        else:
            listed = (' and '.join(missing) if len(missing) == 2
                      else ', '.join(missing[:-1]) + ' and ' + missing[-1])
            hint = f'We need your {listed} before you can submit.'
        return {'label': f'Finish your profile ({len(missing)} to go)',
                'url': f"{reverse('gallery:artist_edit', kwargs={'pk': artist.pk})}?{qs}",
                'hint': hint, 'step': 2}

    submitted = ArtworkSubmission.objects.filter(show=show, artwork__artists=artist).count()
    if submitted:
        return {'label': 'Submit another work', 'url': submit_url,
                'hint': f'You have submitted {submitted} '
                        f'work{"s" if submitted != 1 else ""} to this show.',
                'step': 3}
    return {'label': 'Submit Artwork', 'url': submit_url,
            'hint': 'Upload your work and send it in.', 'step': 3}


def submit_ctas(request, shows):
    """{show_id: cta} for a list of shows, looking the artist up once.

    Only shows currently accepting submissions produce a CTA, so a page of past shows
    costs nothing.
    """
    artist = artist_for(request.user)
    out = {}
    for show in shows:
        cta = submit_cta(request, show, artist=artist, artist_loaded=True)
        if cta:
            out[show.id] = cta
    return out
