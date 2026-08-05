"""One create per rendered form, in one place.

Four forms make artworks or artists — the artwork page, the artist page, adding a piece on
an artist's behalf, and the quick form inside the submit flow — and all four have the same
problem: they upload a photograph, so the request takes seconds with nothing on screen to
say so, somebody clicks again, and a second click makes a second thing. A back-button
resubmit does it too, and no script on the page can see that one.

The rule is small and easy to get subtly wrong, which is why it lives here rather than being
written out four times:

* a form carries a random token, regenerated per render;
* a save keeps it, in a unique column, so the database is what enforces this;
* a POST whose token has already made something, **with the same content**, is a replay and
  is answered with what it made;
* a POST whose token has already made something with *different* content is not a replay —
  it is somebody who went Back and typed a second piece into a restored page — and it must
  be created, because discarding it loses real work silently.

That last case is the one worth the file. It was written the obvious way first, without the
content check, and it threw away a genuine second artwork with a message saying it had
already been saved.
"""
import uuid


def new_token():
    return uuid.uuid4().hex


def token_from(request):
    return (request.POST.get('create_token') or '').strip()[:64]


def prior_for(model, token, submitted, fields):
    """What this token already made, and whether this POST is a replay of it.

    Returns `(existing, is_replay)`. `existing` is None when the token is unused.

    `fields` are what identify a submission: a replay of one POST has all of them the same,
    because it is the same bytes. Deliberately never the uploaded image — re-picking the
    same file is normal, and comparing uploads would make a replay look new.
    """
    if not token:
        return None, False
    existing = model.objects.filter(create_token=token).first()
    if existing is None:
        return None, False
    replay = all(getattr(existing, f) == submitted.get(f) for f in fields)
    return existing, replay


def token_to_store(existing, is_replay, token):
    """The value to save on a new object.

    None when the token is already spent on something else: the object being created is
    real and needs saving, it just cannot claim a token that is taken.
    """
    if existing is not None and not is_replay:
        return None
    return token or None


# What identifies an artwork submission, and an artist one.
ARTWORK_FIELDS = ('name', 'end_year', 'medium', 'width_inches', 'height_inches')
ARTIST_FIELDS = ('first_name', 'last_name', 'email')
