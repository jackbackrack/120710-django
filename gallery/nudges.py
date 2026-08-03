"""Working out what an invited artist still has to do, and telling them.

An invitation-only show is a chain of five steps, and an artist can stall at any of them:
create an account, make an artist profile, finish that profile, add an artwork, submit it.
The gallery could see the stall on the invite page but had only one blunt reminder to
answer it with — the same "please submit your work" to everyone, whether they had never
signed up or had a finished profile and simply not pressed the last button.

So the step is computed per person, from the same fields the invite table shows, and the
email names it. `SUBMIT_REQUIRED` is shared with `submission_cta`, so "a finished profile"
means one thing across the nudge, the table and the submit gate itself — it was defined
twice before this, once here as `image and first_name and last_name and zipcode`.

Nobody who has submitted is ever nudged: they are finished, and the whole point is to
write to people for whom there is something left to say.
"""
from gallery.submission_cta import SUBMIT_REQUIRED

# In order. The first one that applies is what the artist is asked to do next, so a person
# with no account is never told to add artwork they cannot yet attach to anything.
STEP_ACCOUNT = 'account'
STEP_PROFILE = 'profile'
STEP_DETAILS = 'details'
STEP_ARTWORK = 'artwork'
STEP_SUBMIT = 'submit'


def _listed(names):
    """"a, b and c" — for naming missing fields or several curators in a sentence."""
    names = [str(n) for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ''
    return f'{", ".join(names[:-1])} and {names[-1]}'


def next_step(*, has_account, artist, artworks_count, submitted_count):
    """What this artist has left to do, or None if nothing.

    Takes the same values the invite page already computes rather than re-deriving them,
    so the table and the email cannot disagree about who is stuck where.
    """
    if submitted_count:
        return None

    if not has_account:
        return {
            'key': STEP_ACCOUNT,
            'short': 'No account yet',
            'ask': 'create an account and open your personal invitation link',
            'why': 'The link below ties the invitation to your account whichever email '
                   'address you sign up with.',
        }

    if artist is None:
        return {
            'key': STEP_PROFILE,
            'short': 'No artist profile',
            'ask': 'set up your artist profile',
            'why': 'It is what your work is credited to, and it takes a couple of minutes.',
        }

    missing = [label for field, label in SUBMIT_REQUIRED
               if not getattr(artist, field, None)]
    if missing:
        return {
            'key': STEP_DETAILS,
            'short': f'Profile missing {_listed(missing)}',
            'ask': f'add your {_listed(missing)} to your artist profile',
            'why': ('Your photo is printed beside your work in the show catalogue; a phone '
                    'snapshot is fine.' if 'photo' in missing else
                    'These are needed before the submission form will open.'),
        }

    if not artworks_count:
        return {
            'key': STEP_ARTWORK,
            'short': 'No artwork uploaded',
            'ask': 'add the artwork you would like to show',
            'why': 'Title, year, medium, dimensions and a photograph of the piece itself.',
        }

    return {
        'key': STEP_SUBMIT,
        'short': 'Uploaded but not submitted',
        'ask': 'submit your work to the show',
        'why': f'Your {"artwork is" if artworks_count == 1 else "artworks are"} already '
               f'uploaded — this is the last step, and it is one click.',
    }


def curator_names(show):
    """Who the nudge is sent on behalf of. All of them, not just the first."""
    return _listed([c.full_name or c.name for c in show.ordered_curators])


def outstanding(rows):
    """(row, step) for everyone with something left to do, in table order.

    `rows` are the invite page's own `invitation_rows`, so this adds no second source of
    truth about who has done what.
    """
    out = []
    for row in rows:
        step = next_step(
            has_account=row['has_account'],
            artist=row['artist'],
            artworks_count=row['artworks_count'],
            submitted_count=row['submitted_count'],
        )
        if step is not None:
            out.append((row, step))
    return out
