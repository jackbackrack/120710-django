"""Telling a photograph from a placeholder.

The profile photo is required before an artist can submit, and the reason is practical: chasing
photos after acceptance is miserable, so the cost is paid up front. A requirement satisfied by a
coloured square with a letter on it costs the same chasing later, except nobody knows it is coming
— the field is filled in, the form passes, and the problem surfaces when the catalogue prints.

Google pictures are no longer imported at all, which removed the main source of these and is the
better fix — an account with no photo of its own still has a monogram, and even a genuine Google
photo is one the artist never chose for a printed catalogue.

What remains is uploads: a solid square gets past a field that only checks something was chosen.
That is what this is for now.

What this measures is flatness, not faces. A monogram is a background colour, a glyph, and the
antialiasing between them; a photograph — even a head against a white studio wall — carries
hundreds of near-shades. Counting the colours that hold at least one percent of the image
separates them with a wide margin:

    monogram                 1–4 colours
    solid colour             1
    portrait, white wall     10
    portrait, grey wall       9

It cannot tell whether the photograph is of the right person. Nothing can, short of asking.

Measured, rather than assumed:

    Google monogram          0.93 dominant, 3 colours   rejected
    solid white square       1.00 dominant, 1 colour    rejected
    silhouette, black/white  0.70 dominant, 2 colours   accepted
    high-key pale portrait   0.70 dominant, 3 colours   accepted
    portrait, plain wall     0.68 dominant, 6 colours   accepted

The dominance test does the work, not the colour count: a monogram's letter is small so its
background holds ninety per cent of the frame, while a real subject fills it. That is what lets a
two-colour silhouette through. About 6ms per check, once, on upload.
"""
import logging

logger = logging.getLogger(__name__)

# A colour has to hold this much of the image to count as one of its colours, which discards
# antialiasing fringes and JPEG noise around a hard edge.
MIN_SHARE = 0.01
# At or below this many real colours, with one of them dominating, it is a placeholder. Portraits
# measured 9 and up, so there is room either side.
MAX_COLOURS = 5
MIN_DOMINANCE = 0.80


def photo_colours(source):
    """(dominant share, number of colours holding at least MIN_SHARE), or None if unreadable."""
    try:
        from PIL import Image

        if hasattr(source, 'read'):
            source.seek(0)
            image = Image.open(source)
        else:
            image = Image.open(source)
        # Small enough to be quick, large enough that a glyph does not vanish. Quantised to 16
        # levels a channel so JPEG noise does not read as detail.
        small = image.convert('RGB').resize((64, 64))
        counts = {}
        for pixel in ((r // 16, g // 16, b // 16) for r, g, b in small.getdata()):
            counts[pixel] = counts.get(pixel, 0) + 1
        total = sum(counts.values())
        dominant = max(counts.values()) / total
        substantial = sum(1 for n in counts.values() if n >= total * MIN_SHARE)
        return dominant, substantial
    except Exception:   # noqa: BLE001 — an unreadable file is somebody else's error to report
        logger.debug('Could not measure image colours', exc_info=True)
        return None
    finally:
        if hasattr(source, 'seek'):
            try:
                source.seek(0)
            except Exception:   # noqa: BLE001
                pass


def looks_like_placeholder(source):
    """Whether this is a monogram or a flat colour rather than a photograph.

    Answers False when the image cannot be read: refusing a photo because it could not be
    measured would be the worse mistake, and the field's own validation will catch a broken file.
    """
    measured = photo_colours(source)
    if measured is None:
        return False
    dominant, substantial = measured
    return substantial <= MAX_COLOURS and dominant >= MIN_DOMINANCE
