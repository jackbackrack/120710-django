"""Printable exhibition CHECKLIST PDF for a show (Personal Space style).

Cover page (title, curator, dates, opening, participating-artist list, statement)
→ work entries (thumbnail + artist / title(year) / medium / dimensions / price)
→ Artist Bios (photo with the bio flowing around it) → the curator's bio.
Every page has a footer with the site logo and site info.

Separate from the Avery placard sheets (gallery.views.placards)."""
import functools
import io
import logging
from xml.sax.saxutils import escape

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404

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


def _read(field):
    """Bytes for a Django image field / imagekit spec via its storage, or None."""
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


def _downscale(field, max_px):
    """Return (BytesIO JPEG, w, h) downscaled to max_px, or None."""
    raw = _read(field)
    if not raw:
        return None
    try:
        from PIL import Image as PILImage
        im = PILImage.open(io.BytesIO(raw))
        im.load()
        if im.mode not in ('RGB', 'L'):
            im = im.convert('RGB')
        im.thumbnail((max_px, max_px))
        out = io.BytesIO()
        im.save(out, format='JPEG', quality=80)
        out.seek(0)
        return out, im.size[0], im.size[1]
    except Exception:   # noqa: BLE001
        return None


def _img_flowable(field, box_w, box_h, max_px=600):
    """A platypus Image scaled to fit within (box_w, box_h), or None."""
    got = _downscale(field, max_px)
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
    """Split a free-text statement into paragraphs (blank line = new paragraph)."""
    out = []
    for block in (text or '').replace('\r\n', '\n').split('\n\n'):
        block = block.strip()
        if block:
            out.append(Paragraph(escape(block).replace('\n', '<br/>'), style))
    return out


def _bio_entry(person, styles, story):
    """Append a bio block: the photo with the name+bio text flowing around it."""
    bio_text = (person.bio or person.statement or '').strip()
    if not bio_text and not person.instagram:
        return
    handle = f'  {escape(person.instagram)}' if person.instagram else ''
    flows = [
        Paragraph(escape(str(person)), styles['bioname']),
        Paragraph(escape(bio_text).replace('\n', '<br/>') + handle, styles['bio']),
    ]
    img = _img_flowable(getattr(person, 'image', None), 1.3 * inch, 1.6 * inch, max_px=400)
    if img:
        story.append(ImageAndFlowables(img, flows, imageSide='left',
                                       imageRightPadding=10, imageBottomPadding=6))
    else:
        story.extend(flows)
    story.append(Spacer(1, 12))


def _footer(canvas, doc, site):
    canvas.saveState()
    y = 0.42 * inch
    left, right = doc.leftMargin, PAGE_W - doc.rightMargin
    x = left
    logo = None
    if site:
        logo_bytes = _read(getattr(site, 'icon', None)) or _read(getattr(site, 'image', None))
        if logo_bytes:
            try:
                logo = ImageReader(io.BytesIO(logo_bytes))
            except Exception:   # noqa: BLE001
                logo = None
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


def _cover(show, site, works, styles):
    story = [Paragraph(escape(show.name), styles['title'])]
    curators = list(show.curators.all())
    if curators:
        names = ', '.join(str(c) for c in curators)
        story.append(Paragraph('Curated by ' + escape(names), styles['curator']))
    story.append(Paragraph(escape(str(show)), styles['meta']))   # date range via Show.__str__
    for ev in show.events.all().order_by('date', 'start'):
        when = ev.date.strftime('%A %B %-d, %Y')
        times = f"{ev.start.strftime('%-I:%M %p').lower()}–{ev.end.strftime('%-I:%M %p').lower()}"
        story.append(Paragraph(f'{escape(ev.name)}: {when}, {times}', styles['meta']))

    # Participating-artist list (two columns).
    seen, artists = set(), []
    for w in works:
        for a in w.artists.all():
            if a.pk not in seen:
                seen.add(a.pk)
                artists.append(str(a))
    if artists:
        half = (len(artists) + 1) // 2
        col1, col2 = artists[:half], artists[half:]
        rows = []
        for i in range(half):
            rows.append([Paragraph(escape(col1[i]), styles['names']),
                         Paragraph(escape(col2[i]) if i < len(col2) else '', styles['names'])])
        t = Table(rows, colWidths=[3.25 * inch, 3.25 * inch])
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 0),
                               ('TOPPADDING', (0, 0), (-1, -1), 2)]))
        story.append(Spacer(1, 10))
        story.append(t)

    story.extend(_statement_flowables(show.description, styles['stmt']))
    return story


def _work_entry(artwork, styles, content_w):
    artists = ', '.join(str(a) for a in artwork.artists.all())
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
    img = _img_flowable(artwork.image, img_w, 1.5 * inch, max_px=600)
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
    works.sort(key=lambda a: (', '.join(str(x) for x in a.artists.all()).lower(),
                              numbers.get(a.id, 10 ** 9), (a.name or '').lower()))

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
        story = _cover(show, site, works, styles)

        if works:
            story.append(PageBreak())
            for art in works:
                story.append(_work_entry(art, styles, content_w))

        # Artist bios, then the curator's bio.
        bio_artists, seen = [], set()
        for w in works:
            for a in w.artists.all():
                if a.pk not in seen and (a.bio or a.statement):
                    seen.add(a.pk)
                    bio_artists.append(a)
        if bio_artists:
            story.append(PageBreak())
            story.append(Paragraph('Artist Bios', styles['section']))
            for a in bio_artists:
                _bio_entry(a, styles, story)

        curators = [c for c in show.curators.all() if (c.bio or c.statement)]
        if curators:
            story.append(Paragraph('Curator', styles['section']))
            for c in curators:
                _bio_entry(c, styles, story)

        footer = functools.partial(_footer, site=site)
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
