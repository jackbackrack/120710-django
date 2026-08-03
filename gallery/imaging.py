"""How a source photograph becomes the versions the site shows.

One place, because there are twenty spec fields across four models and they must agree.
Before this, each repeated `[Transpose(), ResizeToFit(...)]` by hand — and every one of
them silently threw the colour away.

## The colour problem this exists to fix

A photograph of artwork arrives from a photographer's workflow tagged **Adobe RGB
(1998)**, or occasionally ProPhoto or Display P3. The numbers in the file mean "this
colour, measured in that space".

Pillow reads the pixels and drops the profile. The derived JPEG therefore carries no
profile at all, and a browser shown an untagged image assumes sRGB — a narrower space.
The same numbers now mean a *different, duller* colour, and every saturated area shifts.

Measured on a cyanotype in a real show: the red channel was wrong by up to **76 levels
out of 255**, and 29% of the image was off by more than 10. Deep blues came out grey-ish
and washed. Greys and whites were untouched, which is why it reads as "the compression
ruined the colour" rather than as a colour-management fault — flat areas look fine and
only the artwork itself is wrong.

`ToSRGB` converts through the embedded profile so the *appearance* survives, rather than
the raw numbers. Nothing else in the pipeline touches colour.
"""
import io
import logging

from imagekit.processors import ResizeToFit, Transpose
from PIL import Image, ImageCms

logger = logging.getLogger(__name__)


# Which image modes each kind of profile can actually describe. Anything else — a Lab
# profile on RGB pixels, a CMYK profile on data read as RGB — cannot be built into a
# transform, and is a mis-tagged file rather than a colour space we could honour.
_PROFILE_MODES = {'RGB': {'RGB'}, 'GRAY': {'L'}, 'CMYK': {'CMYK'}}


def _profile_space(profile):
    """The profile's own colour space, e.g. 'RGB', 'CMYK', 'GRAY' — or '' if unreadable."""
    try:
        return (profile.profile.xcolor_space or '').strip().upper()
    except Exception:                                         # noqa: BLE001
        return ''


def _profile_describes(profile, mode):
    return mode in _PROFILE_MODES.get(_profile_space(profile), ())


class ToSRGB:
    """Convert to sRGB through the image's own ICC profile.

    Deliberately converts rather than passing the profile through to the output. Both
    would be correct in a colour-managed browser, but converting is right in every
    browser: an untagged image is assumed to be sRGB by all of them, so once the numbers
    *are* sRGB the picture is right whether or not anything reads profiles. It also keeps
    a 200px thumbnail from carrying a few KB of profile it does not need.

    An image with no profile is left alone: untagged already means sRGB by convention,
    and "converting" it would be inventing information.
    """

    def process(self, img):
        icc = (img.info or {}).get('icc_profile')
        if not icc:
            return img
        # Alpha is carried around the conversion rather than through it. LittleCMS returns
        # RGB, so converting a transparent PNG directly would drop the channel and put a
        # black background behind every site logo — the icon specs output PNG for exactly
        # that reason.
        alpha = None
        working = img
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            working = img.convert('RGBA')
            alpha = working.getchannel('A')
            working = working.convert('RGB')
        try:
            source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if not _profile_describes(source, working.mode):
                # A tag that does not match the pixels is not information, it is noise —
                # a Lab or CMYK profile on data Pillow read as RGB. LittleCMS answers this
                # with "cannot build transform", and there is no correct conversion to
                # attempt: treating the numbers as sRGB is exactly as right as before.
                # Logged flatly rather than as a traceback, because a bulk regeneration
                # will hit it once per spec per image and the noise buries real faults.
                logger.info('Ignoring an ICC profile (%s) that does not describe a %s '
                            'image; leaving its colour alone',
                            _profile_space(source) or 'unreadable', working.mode)
                return img
            converted = ImageCms.profileToProfile(
                working, source, ImageCms.createProfile('sRGB'), outputMode='RGB')
            if converted is not None and alpha is not None:
                converted.putalpha(alpha)
        except Exception:
            # A malformed or unsupported profile must not cost the gallery an image. The
            # untouched picture is wrong in the way it was already wrong, which is better
            # than no picture at all — but it is worth knowing about.
            logger.warning('Could not convert an image to sRGB; leaving it as it is',
                           exc_info=True)
            return img
        if converted is None:
            return img
        # The profile has been applied, so carrying it forward would apply it twice.
        converted.info.pop('icc_profile', None)
        return converted


def web_processors(width, height=None):
    """The processor list every derived image on the site uses.

    Order matters. Transpose first, so an EXIF-rotated phone photo is upright before
    anything measures it; ToSRGB before the resize, so the conversion runs on the full
    data rather than on interpolated pixels.
    """
    return [Transpose(), ToSRGB(), ResizeToFit(width=width, height=height)]


__all__ = ['ToSRGB', 'web_processors', 'Image']
