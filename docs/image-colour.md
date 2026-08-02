# Image colour

Artwork photographs arrive from a photographer's workflow tagged **Adobe RGB (1998)**,
sometimes ProPhoto or Display P3. Everything the site shows is a derived JPEG, and those
derivatives have to end up in **sRGB** or the colour is wrong.

## What was happening

Pillow reads the pixels and drops the ICC profile. The derived JPEG carried no profile at
all, and a browser assumes an untagged image is sRGB — a narrower space. The numbers were
unchanged; their *meaning* was not.

Measured on a cyanotype in a real show (900×1200 source, Adobe RGB):

| | |
| --- | --- |
| max error, red channel | **76 levels out of 255** |
| image off by >10 levels | **29%** |
| deep blue, shown vs correct | `(64, 118, 165)` vs `(0, 119, 168)` |
| concrete floor | `(104, 105, 107)` — unchanged |
| white wall | `(212, 212, 214)` — unchanged |

Greys and whites are nearly identical between the two spaces, so flat areas looked fine
and only the saturated parts — the artwork itself — were wrong. That is why it reads as
"the compression ruined the colour" rather than as a colour-management fault.

**Nothing was lost in compression.** Originals are stored untouched, byte for byte; only
the derivatives were being misinterpreted.

## The fix

`gallery/imaging.py` defines `ToSRGB`, and `web_processors(width, height=None)` is the
processor list every spec field on the site uses:

    [Transpose(), ToSRGB(), ResizeToFit(...)]

Order matters. Transpose first, so an EXIF-rotated phone photo is upright before anything
measures it; ToSRGB before the resize, so the conversion runs on real data rather than
interpolated pixels.

Two decisions worth keeping:

- **Convert, don't embed.** Passing the profile through would also be correct in a
  colour-managed browser, but converting is right in *every* browser, since untagged is
  assumed to be sRGB everywhere. It also keeps a 200px thumbnail from carrying several KB
  of profile it does not need.
- **Untagged images are left alone.** Untagged already means sRGB by convention;
  "converting" one would be inventing information.

Alpha is carried *around* the conversion, not through it: LittleCMS returns RGB, so
converting a transparent PNG directly drops the channel and puts a black background behind
every site logo. The `icon_*` specs output PNG for exactly that reason.

A malformed profile logs a warning and returns the image untouched. A wrong picture beats
no picture.

## Deploying a change to any spec

**This is the part that will bite.** imagekit's cache filename is a hash of
`(source name, processors, format, options, autoconvert)` — see
`imagekit/specs/__init__.py::get_hash`. Change a processor and **every derivative on the
site gets a new filename.**

That is what makes the fix take effect, and it is also the hazard, because
`IMAGEKIT_DEFAULT_CACHEFILE_STRATEGY` is `eatart.imagekit_strategy.Optimistic`, which
**skips existence checks** and only generates on source save. Existing artworks will not be
re-saved, so the new filenames will not exist and **every image on the site 404s** until
they are generated.

So a spec change is a two-part deploy:

    ./env/bin/python manage.py generateimages

Run it immediately after deploying — ideally as part of the same release step. It walks
every registered spec and writes the missing files. Expect it to take a while against S3
and to cost one PUT per derivative; there are 22 specs.

The old files are orphaned rather than overwritten. They cost storage and nothing else,
and can be swept later.
