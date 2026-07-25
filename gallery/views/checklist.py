"""Printable exhibition CHECKLIST PDF for a show (Personal Space style).

Cover page (title, curator, dates, opening, participating-artist list, statement)
→ work entries (thumbnail + artist / title(year) / medium / dimensions / price)
→ Artist Bios (photo with the bio flowing around it) → the curator's bio.
Every page has a footer with the site logo and site info.

Separate from the Avery placard sheets (gallery.views.placards)."""
import functools
import html as _html
import io
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.html import strip_tags

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    Image, ImageAndFlowables, KeepTogether, PageBreak, Paragraph,
    SimpleDocTemplate, Spacer, Table, TableStyle,
)

from gallery.models import Show
from gallery.models.show_artwork_numbers import ShowArtworkNumber
from gallery.permissions import can_manage_show
# Reuse the Unicode font chosen (and registered) by the placard module.
from gallery.views.placards import _BOLD, _FONT, _ITALIC

logger = logging.getLogger(__name__)

PAGE_W, PAGE_H = letter

# Let <b>/<i> markup in paragraphs map to the right TrueType variants.
if _FONT != 'Helvetica':
    try:
        pdfmetrics.registerFontFamily(_FONT, normal=_FONT, bold=_BOLD,
                                      italic=_ITALIC, boldItalic=_BOLD)
    except Exception:   # noqa: BLE001
        pass


def _styles():
    return {
        'title': ParagraphStyle('t', fontName=_BOLD, fontSize=24, leading=27, spaceAfter=4),
        'curator': ParagraphStyle('c', fontName=_ITALIC, fontSize=12, leading=15, spaceAfter=2),
        'meta': ParagraphStyle('m', fontName=_FONT, fontSize=11, leading=14),
        'names': ParagraphStyle('n', fontName=_FONT, fontSize=11, leading=15),
        'stmt': ParagraphStyle('s', fontName=_FONT, fontSize=10.5, leading=14, spaceBefore=10),
        'section': ParagraphStyle('h', fontName=_BOLD, fontSize=13, leading=16,
                                  spaceBefore=16, spaceAfter=8),
        'work': ParagraphStyle('w', fontName=_FONT, fontSize=9.5, leading=12.5),
        'bioname': ParagraphStyle('bn', fontName=_BOLD, fontSize=11, leading=14, spaceAfter=1),
        'bio': ParagraphStyle('b', fontName=_FONT, fontSize=9.5, leading=13, alignment=TA_LEFT),
    }


def _plain(text):
    """Strip HTML to readable plain text: block tags → newlines, tags removed,
    entities decoded. (Show descriptions / bios / statements may hold rich text.)"""
    if not text:
        return ''
    t = text.replace('</p>', '\n\n').replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
    t = strip_tags(t)
    t = _html.unescape(t)
    return t.strip()


def _ptext(text):
    """Plain text (HTML stripped) escaped for a reportlab Paragraph, newlines → <br/>."""
    return escape(_plain(text)).replace('\n', '<br/>')


# A checklist reads one image per work, one per artist/curator bio, plus the show
# image and the footer logo — for a 40-work show that is ~80 S3 round trips. Done
# serially they dominate the build (tens of seconds on a single gunicorn worker,
# stalling the whole site). They are pure network waits, so a small thread pool
# collapses them; the GIL is released for socket I/O.
PREFETCH_WORKERS = 8


def _fetch(field):
    """Bytes for a Django image field / imagekit spec via its storage, or None.
    Safe to call from a worker thread: it touches storage only, never the ORM, and
    each field object is fetched by exactly one thread."""
    if not field:
        return None
    try:
        field.open('rb')
        try:
            return field.read()
        finally:
            field.close()
    except Exception:   # noqa: BLE001 — a missing/broken image must never break the PDF
        return None


def _read(field, cache=None):
    """_fetch() with an optional {storage name: bytes} memo, so a prefetched image
    is not fetched a second time."""
    if not field:
        return None
    key = getattr(field, 'name', None)
    if cache is not None and key and key in cache:
        return cache[key]
    data = _fetch(field)
    if cache is not None and key:
        cache[key] = data
    return data


def _prefetch(candidate_lists, cache):
    """Warm `cache` with the PRIMARY candidate of each image slot, concurrently.

    Only the first candidate — the small thumbnail spec — is prefetched. The later
    candidates are full-resolution originals used as a fallback; fetching those
    up front would download megabytes that are almost never needed. A slot whose
    thumbnail is missing falls back serially in _downscale, and is logged there."""
    todo, seen = [], set()
    for fields in candidate_lists:
        first = next((f for f in fields if f), None)
        key = getattr(first, 'name', None) if first is not None else None
        if not key or key in seen:
            continue
        seen.add(key)
        todo.append((key, first))
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=min(PREFETCH_WORKERS, len(todo))) as pool:
        for key, data in pool.map(lambda kf: (kf[0], _fetch(kf[1])), todo):
            cache[key] = data
    missing = sum(1 for v in cache.values() if not v)
    logger.info('Checklist prefetch: %d image(s), %d unreadable', len(todo), missing)


def _downscale(fields, max_px, cache=None):
    """Return (BytesIO JPEG, w, h) for the first readable field, downscaled to
    max_px. `fields` may be a single field or a list of candidates (e.g. a small
    thumbnail spec first, then the full image as a fallback)."""
    if not isinstance(fields, (list, tuple)):
        fields = [fields]
    for i, field in enumerate(fields):
        raw = _read(field, cache)
        if not raw:
            continue
        if i:
            # Past the first candidate: the thumbnail spec was missing, so this is a
            # full-resolution original. Costly and usually a sign the spec was never
            # generated for that image — worth seeing in the logs.
            logger.warning('Checklist: thumbnail missing for %s, fell back to '
                           'candidate %d (%s, %d bytes)',
                           getattr(fields[0], 'name', '?'), i,
                           getattr(field, 'name', '?'), len(raw))
        try:
            from PIL import Image as PILImage, ImageOps
            im = PILImage.open(io.BytesIO(raw))
            im.load()
            im = ImageOps.exif_transpose(im)   # honor EXIF orientation (phone photos)
            if im.mode not in ('RGB', 'L'):
                im = im.convert('RGB')
            im.thumbnail((max_px, max_px))
            out = io.BytesIO()
            im.save(out, format='JPEG', quality=80)
            out.seek(0)
            return out, im.size[0], im.size[1]
        except Exception:   # noqa: BLE001
            continue
    return None


def _img_flowable(fields, box_w, box_h, max_px=600, cache=None):
    """A platypus Image scaled to fit within (box_w, box_h), or None. `fields` may
    be a single image field or a list of candidates."""
    got = _downscale(fields, max_px, cache)
    if not got:
        return None
    data, iw, ih = got
    scale = min(box_w / iw, box_h / ih)
    return Image(data, width=iw * scale, height=ih * scale)


def _years(artwork):
    sy, ey = artwork.start_year, artwork.end_year
    if ey and sy and sy != ey:
        return f'{sy}–{ey}'
    return str(ey or sy or '')


def _para_lines(*lines, style):
    text = '<br/>'.join(l for l in lines if l)
    return Paragraph(text, style)


def _statement_flowables(text, style):
    """HTML-stripped free-text split into paragraphs (blank line = new paragraph)."""
    out = []
    for block in _plain(text).split('\n\n'):
        block = block.strip()
        if block:
            out.append(Paragraph(escape(block).replace('\n', '<br/>'), style))
    return out


def _bio_entry(person, styles, story, cache=None):
    """A bio block: the photo with the name + bio + statement flowing around it.
    The Instagram handle sits right after the name; 'Bio:' and 'Statement:' are
    bolded labels. Always shows the name and photo, even with no bio/statement."""
    name = f'<b>{escape(str(person))}</b>'
    if person.instagram:
        name += f'  {escape(person.instagram)}'
    flows = [Paragraph(name, styles['bioname'])]
    if (person.bio or '').strip():
        flows.append(Paragraph(_ptext(person.bio), styles['bio']))   # no label — the bio is obvious
    if (person.statement or '').strip():
        if (person.bio or '').strip():
            flows.append(Spacer(1, 6))   # line break between bio and statement
        flows.append(Paragraph('<b>Statement:</b> ' + _ptext(person.statement), styles['bio']))
    img = _img_flowable([getattr(person, 'card_md', None), getattr(person, 'image', None)],
                        1.3 * inch, 1.6 * inch, max_px=600, cache=cache)   # medium source, same on-paper size
    if img:
        story.append(ImageAndFlowables(img, flows, imageSide='left',
                                       imageRightPadding=10, imageBottomPadding=6))
    else:
        story.extend(flows)
    story.append(Spacer(1, 12))


def _logo_reader(site, cache=None):
    """Read the site LOGO (site.icon) once, as an ImageReader reused on every page.
    (Reading per page re-opens storage, which on S3 can come back empty after the
    first page — so the footer logo vanished from page 2 on.)"""
    if not site:
        return None
    data = _read(getattr(site, 'icon', None), cache)
    if not data:
        return None
    try:
        return ImageReader(io.BytesIO(data))
    except Exception:   # noqa: BLE001
        return None


def _footer(canvas, doc, site, logo):
    canvas.saveState()
    y = 0.42 * inch
    left, right = doc.leftMargin, PAGE_W - doc.rightMargin
    x = left
    if logo:
        canvas.drawImage(logo, left, y - 3, width=0.4 * inch, height=0.4 * inch,
                         preserveAspectRatio=True, mask='auto', anchor='sw')
        x = left + 0.5 * inch
    canvas.setFont(_FONT, 7.5)
    canvas.setFillGray(0.3)
    if site:
        canvas.drawString(x, y + 9, site.website or site.name or '')
        canvas.drawString(x, y, site.instagram or '')
        addr1 = site.street or ''
        loc = ' '.join(p for p in [site.state, site.postal_code] if p)
        addr2 = ', '.join(p for p in [site.city, loc] if p)
        canvas.drawRightString(right, y + 9, addr1)
        canvas.drawRightString(right, y, addr2)
    canvas.restoreState()


def _cover(show, site, works, styles, content_w, cache=None):
    story = [Paragraph(escape(show.name), styles['title'])]
    curators = list(show.curators.all())
    if curators:
        names = ', '.join(str(c) for c in curators)
        story.append(Paragraph('Curated by ' + escape(names), styles['curator']))
    story.append(Paragraph(escape(show.date_range), styles['meta']))   # dates (str(show) is the name)
    for ev in show.events.all().order_by('date', 'start'):
        when = ev.date.strftime('%A %B %-d, %Y')
        times = f"{ev.start.strftime('%-I:%M %p').lower()}–{ev.end.strftime('%-I:%M %p').lower()}"
        story.append(Paragraph(f'{escape(ev.name)}: {when}, {times}', styles['meta']))

    # Participating-artist list — as many columns as needed for the count.
    seen, artists = set(), []
    for w in works:
        for a in w.credited_artists:
            if a.pk not in seen:
                seen.add(a.pk)
                artists.append(str(a))
    artists.sort(key=str.casefold)   # a cover list reads as a roster, so alphabetical
    if artists:
        n = len(artists)
        ncols = 2 if n <= 14 else 3 if n <= 30 else 4
        per = math.ceil(n / ncols)
        cols = [artists[i * per:(i + 1) * per] for i in range(ncols)]
        rows = []
        for ri in range(per):
            rows.append([Paragraph(escape(cols[ci][ri]) if ri < len(cols[ci]) else '', styles['names'])
                         for ci in range(ncols)])
        t = Table(rows, colWidths=[content_w / ncols] * ncols)
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 0),
                               ('TOPPADDING', (0, 0), (-1, -1), 2)]))
        story.append(Spacer(1, 10))
        story.append(t)

    # Show image (high-res source, capped ~1600px) with the description flowing
    # around it — same on-paper size as before.
    show_img = _img_flowable([show.slideshow, show.detail_lg, show.image],
                             3.0 * inch, 3.5 * inch, max_px=1600, cache=cache)
    desc = _statement_flowables(show.description, styles['stmt'])
    story.append(Spacer(1, 12))
    if show_img:
        story.append(ImageAndFlowables(show_img, desc or [Spacer(1, 1)], imageSide='left',
                                       imageRightPadding=12, imageBottomPadding=8))
    else:
        story.extend(desc)
    return story


def _work_entry(artwork, styles, content_w, cache=None):
    artists = artwork.credit_line
    title_year = escape(artwork.name or 'Untitled')
    yr = _years(artwork)
    if yr:
        title_year = f'<i>{title_year}</i>, {escape(yr)}'
    else:
        title_year = f'<i>{title_year}</i>'
    para = _para_lines(
        f'<b>{escape(artists)}</b>' if artists else '',
        title_year,
        escape(artwork.medium or ''),
        escape(artwork.placard_dimensions or artwork.formatted_dimensions or ''),
        escape(artwork.formatted_price or ''),
        style=styles['work'],
    )
    img_w = 1.5 * inch
    img = _img_flowable([artwork.card_md, artwork.image], img_w, 1.5 * inch, max_px=600, cache=cache)   # medium source, same on-paper size
    if img:
        row = Table([[img, para]], colWidths=[img_w + 0.15 * inch, content_w - img_w - 0.15 * inch])
        row.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                 ('LEFTPADDING', (0, 0), (0, 0), 0),
                                 ('LEFTPADDING', (1, 0), (1, 0), 6),
                                 ('TOPPADDING', (0, 0), (-1, -1), 0),
                                 ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
        return KeepTogether([row, Spacer(1, 12)])
    return KeepTogether([para, Spacer(1, 12)])


@login_required
def show_checklist_pdf(request, slug):
    """Download the show's checklist as a PDF (Personal Space style)."""
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404

    site = show.sites.first()
    numbers = {sn.artwork_id: sn.number
               for sn in ShowArtworkNumber.objects.filter(show=show)}
    works = list(show.artworks.prefetch_related('artists'))
    works.sort(key=lambda a: (a.credit_line.casefold(),
                              numbers.get(a.id, 10 ** 9), (a.name or '').lower()))

    # Every participating artist (name + photo + bio if any), then the curator(s).
    artists, seen = [], set()
    for w in works:
        for a in w.credited_artists:
            if a.pk not in seen:
                seen.add(a.pk)
                artists.append(a)
    artists.sort(key=lambda a: str(a).casefold())   # bios in the same order as the cover
    curators = list(show.curators.all())

    # Resolve every image field on THIS thread (field access can touch the ORM), then
    # fetch the bytes concurrently. Worker threads only talk to storage.
    people = artists + curators
    cache = {}
    _prefetch(
        [[show.slideshow, show.detail_lg, show.image]]
        + [[getattr(site, 'icon', None)]]
        + [[a.card_md, a.image] for a in works]
        + [[getattr(p, 'card_md', None), getattr(p, 'image', None)] for p in people],
        cache,
    )

    styles = _styles()
    left = right = 0.75 * inch
    content_w = PAGE_W - left - right

    try:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=letter, leftMargin=left, rightMargin=right,
            topMargin=0.7 * inch, bottomMargin=0.85 * inch,
            title=f'Checklist — {show.name}',
        )
        story = _cover(show, site, works, styles, content_w, cache=cache)

        if works:
            story.append(PageBreak())
            for art in works:
                story.append(_work_entry(art, styles, content_w, cache=cache))

        if artists:
            story.append(PageBreak())
            story.append(Paragraph('Artists', styles['section']))
            for a in artists:
                _bio_entry(a, styles, story, cache=cache)
        if curators:
            story.append(Paragraph('Curator' + ('s' if len(curators) > 1 else ''), styles['section']))
            for c in curators:
                _bio_entry(c, styles, story, cache=cache)

        footer = functools.partial(_footer, site=site, logo=_logo_reader(site, cache))
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
    except Exception as e:   # noqa: BLE001 — surface real failures instead of a bare 500
        logger.exception('Checklist PDF failed for show %s (font=%s)', show.slug, _FONT)
        return HttpResponse(f'Checklist PDF generation failed: {e}', status=500,
                            content_type='text/plain')

    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="checklist-{show.slug}.pdf"'
    resp['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp['Pragma'] = 'no-cache'
    return resp
