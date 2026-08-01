"""The single "what do I do next?" action for a viewer looking at a show.

One function, used wherever a show is displayed — the show page, the home page, the
show list, an artist's own page. Keeping it in one place is the point: the submission
flow only feels seamless if every entry into it says the same thing.

**The button always says Submit, whatever state the reader is in, and always goes to the
submit URL.** Everyone who clicks it wants the same thing; the steps between them and it
are our problem, not theirs. `artwork_submit` is the state machine — it sends somebody
with no profile to create one, somebody with a half-filled one to finish it, and both
back here afterwards — so the button does not need to name the step, and naming it was
actively worse: "Set up your artist profile" reads as a detour, and the person who has an
account but no profile is *further along* than a stranger yet was the only one not
offered the thing they came for.

This used to differ per state, with the labels and destinations of the intermediate steps
on the button. That existed to stop a "Submit" button dead-ending for somebody who could
not yet submit — a real risk when the submit view answered "no profile" with a bare
redirect to the show page. It does not any more, so the reason is gone.

What each state still changes is the **hint**, which says what is about to happen, and the
**step**, which drives the three-part tracker. That is where the hand-holding lives.
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


def profile_next_step(artist, submit_url):
    """Where somebody must go before they can submit, or None if they are ready.

    The counterpart to `submit_cta`, and deliberately NOT the same URL. The button always
    says Submit and always points at the submit view; this is what that view does with
    somebody who is not ready. Routing the view at the *button's* URL instead — which is
    what sharing one URL would mean — is an infinite redirect, since the button now points
    back at the view.

    Beside the CTA rather than in the view because the two have to agree on what "ready"
    means, and `SUBMIT_REQUIRED` is that definition.
    """
    if artist is None:
        return f"{reverse('gallery:artist_new')}?{urlencode({'next': submit_url})}"
    missing = [field for field, _label in SUBMIT_REQUIRED
               if not getattr(artist, field, None)]
    if missing:
        qs = urlencode({'highlight': ','.join(missing), 'next': submit_url})
        return f"{reverse('gallery:artist_edit', kwargs={'pk': artist.pk})}?{qs}"
    return None


def submit_cta(request, show, artist=None, artist_loaded=False):
    """A dict describing the next action, or None if there is nothing to offer.

    Pass `artist` (with artist_loaded=True) when rendering a list of shows, so the
    profile is looked up once rather than per card.

    Returns {label, short_label, url, hint, step}.

    There is one `url`, deliberately. A card short on space gets a shorter *label*, never a
    different destination: the one step where those diverged was profile creation, where the
    short form pointed at the submit page — the exact URL somebody without a profile cannot
    use. Every entry into this flow now goes to the same place the show page sends them.
    """
    from gallery.models import ArtworkSubmission, Show

    if not show.is_accepting_submissions:
        return None

    submit_url = reverse('gallery:artwork_submit', kwargs={'slug': show.slug})
    user = request.user

    # Invitation-only shows offer nothing to anyone who is not a signed-in invitee.
    # Checked before the signed-out branch: telling a stranger to sign up so they can
    # submit to a show they cannot submit to is worse than saying nothing. Invitees
    # arrive through the emailed accept link, which signs them in and returns here.
    if show.submission_type == Show.SUBMISSION_INVITED:
        from gallery.permissions import user_invited_to_show
        if not user.is_authenticated or not user_invited_to_show(show, user):
            return None

    if not user.is_authenticated:
        # The submit view is login-required, so this lands on the sign-in page carrying
        # ?next=, and that page offers sign-up with the destination preserved.
        return {'label': 'Submit', 'url': submit_url,
                'hint': 'You will sign in or create an account first — it takes a minute, '
                        'and you will come straight back here.',
                'short_label': 'Submit',
                'step': 1}

    if not artist_loaded:
        artist = artist_for(user)

    if artist is None:
        return {'label': 'Submit', 'url': submit_url,
                'hint': 'First a quick artist profile so we can credit your work, then you '
                        'add your artwork and send it in.',
                'short_label': 'Submit', 'step': 2}

    missing = [label for field, label in SUBMIT_REQUIRED
               if not getattr(artist, field, None)]
    if missing:
        if missing == ['photo']:
            hint = ('Just a photo of you left — it prints in the show catalogue. A phone '
                    'snapshot is fine, and then you are submitting.')
        else:
            listed = (' and '.join(missing) if len(missing) == 2
                      else ', '.join(missing[:-1]) + ' and ' + missing[-1])
            hint = (f'We need your {listed} first — you will be asked for those, '
                    f'then you are submitting.')
        return {'label': 'Submit', 'url': submit_url, 'hint': hint,
                'short_label': 'Submit', 'step': 2}

    submitted = ArtworkSubmission.objects.filter(show=show, artwork__artists=artist).count()
    if submitted:
        return {'label': 'Submit another work', 'url': submit_url,
                'hint': f'You have submitted {submitted} '
                        f'work{"s" if submitted != 1 else ""} to this show.',
                'short_label': 'Submit another', 'step': 3}
    return {'label': 'Submit', 'url': submit_url,
            'hint': 'Upload your work and send it in.',
            'short_label': 'Submit', 'step': 3}


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
