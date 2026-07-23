import datetime
import io
import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from gallery.models import Show
from gallery.models.show_artwork_numbers import ShowArtworkNumber
from gallery.permissions import can_manage_show


def _current_show():
    today = datetime.date.today()
    show = (
        Show.objects
        .filter(status=Show.STATUS_PUBLISHED, start__lte=today, end__gte=today)
        .order_by('-start')
        .first()
    )
    if not show:
        show = (
            Show.objects
            .filter(status=Show.STATUS_PUBLISHED, start__gt=today)
            .order_by('start')
            .first()
        )
    return show


def _get_placard_data(show, number):
    if show is None:
        return None
    entry = ShowArtworkNumber.objects.filter(show=show, number=number).select_related('artwork').first()
    if entry is None:
        return None
    artwork = entry.artwork
    artists = list(artwork.artists.values_list('name', flat=True))
    year = str(artwork.start_year) + '–' + str(artwork.end_year) if artwork.start_year and artwork.start_year != artwork.end_year else str(artwork.end_year)
    image_url = artwork.card_thumbnail.url if artwork.image else None
    return {
        'number': number,
        'show': show.name,
        'artwork': {
            'name': artwork.name,
            'year': year,
            'medium': artwork.medium or '',
            'dimensions': artwork.formatted_dimensions or '',
            'price': artwork.formatted_price or '',
            'is_sold': artwork.is_sold,
            'description': artwork.description or '',
            'artists': artists,
            'image_url': image_url,
        },
    }


def placard_html(request, number):
    show = _current_show()
    data = _get_placard_data(show, number)
    return render(request, 'gallery/placard.html', {
        'data': data,
        'number': number,
        'show': show,
    })


def placard_json(request, number):
    show = _current_show()
    data = _get_placard_data(show, number)
    if data is None:
        return JsonResponse({'error': 'not found', 'number': number}, status=404)
    return JsonResponse(data)


# ── Printable placard sheets (Avery 5376 business-card stock) ──────────────────
# Avery 5376 == 5371 / 8371 / 8471: US Letter, ten 3.5 x 2 in cards, 2 cols x 5
# rows, 0.75 in side margins, 0.5 in top/bottom, no gutters.
PAGE_W, PAGE_H = letter                       # 612 x 792 pt (1 in = 72 pt)
CARD_W, CARD_H = 3.5 * inch, 2.0 * inch
COLS, ROWS = 2, 5
LEFT_MARGIN = (PAGE_W - COLS * CARD_W) / 2    # 0.75 in
TOP_MARGIN = (PAGE_H - ROWS * CARD_H) / 2     # 0.5 in
PER_PAGE = COLS * ROWS

PAD = 0.16 * inch          # inner margin inside each card
LEADING = 1.28             # line height as a multiple of font size
MIN_FONT = 4.0             # floor so text never disappears

_FONT, _BOLD, _ITALIC = 'Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique'


def _card_lines(artwork, number):
    """Placard lines (text, font, base_size) mirroring the on-screen placard:
    title, artist(s), medium/year, dimensions, price."""
    artists = ', '.join(str(a) for a in artwork.artists.all())
    year = str(artwork.end_year) if artwork.end_year else ''
    medium = (artwork.medium or '').strip()
    medium_year = ' • '.join(p for p in (medium, year) if p)
    rows = []
    if number is not None:
        rows.append((f'#{number}', _FONT, 7.5))
    rows.append((artwork.name or 'Untitled', _BOLD, 12.0))
    if artists:
        rows.append((artists, _FONT, 10.0))
    if medium_year:
        rows.append((medium_year, _ITALIC, 9.0))
    if artwork.placard_dimensions:
        rows.append((artwork.placard_dimensions, _FONT, 9.0))
    price = artwork.formatted_price
    if price:
        rows.append((price, _BOLD, 9.5))
    return [(t, f, s) for (t, f, s) in rows if t]


def _fit(lines, avail_w, avail_h):
    """Shrink fonts so each line fits the width and the block fits the height."""
    out = []
    for text, font, size in lines:
        w = stringWidth(text, font, size)
        if w > avail_w:
            size = max(MIN_FONT, size * avail_w / w)
        out.append([text, font, size])
    total_h = sum(s * LEADING for _, _, s in out)
    if total_h > avail_h and total_h > 0:
        scale = avail_h / total_h
        for ln in out:
            ln[2] *= scale
        for ln in out:   # a vertical shrink can only help width; re-check to be safe
            w = stringWidth(ln[0], ln[1], ln[2])
            if w > avail_w:
                ln[2] *= avail_w / w
    return out


def _draw_card(c, x, y, artwork, number, outline):
    """Draw one placard with bottom-left corner at (x, y)."""
    if outline:
        c.saveState()
        c.setStrokeGray(0.75)
        c.setLineWidth(0.5)
        c.rect(x, y, CARD_W, CARD_H)
        c.restoreState()
    avail_w, avail_h = CARD_W - 2 * PAD, CARD_H - 2 * PAD
    lines = _fit(_card_lines(artwork, number), avail_w, avail_h)
    block_h = sum(s * LEADING for _, _, s in lines)
    cx = x + CARD_W / 2
    cursor = y + CARD_H / 2 + block_h / 2      # top of the vertically-centred block
    c.setFillGray(0)
    for text, font, size in lines:
        cursor -= size * LEADING
        c.setFont(font, size)
        c.drawCentredString(cx, cursor + size * 0.22, text)


@login_required
def placard_sheet_pdf(request, slug):
    """Download a PDF of the show's placards laid out for Avery 5376 sheets.
    ?outlines=1 draws faint card borders for a plain-paper alignment test."""
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404

    outline = request.GET.get('outlines') == '1'
    numbers = {sn.artwork_id: sn.number
               for sn in ShowArtworkNumber.objects.filter(show=show)}
    artworks = list(show.artworks.prefetch_related('artists'))
    artworks.sort(key=lambda a: (numbers.get(a.id, 10 ** 9), (a.name or '').lower()))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, title=f'Placards — {show.name}')
    for i, art in enumerate(artworks):
        slot = i % PER_PAGE
        if i and slot == 0:
            c.showPage()
        col, row = slot % COLS, slot // COLS
        x = LEFT_MARGIN + col * CARD_W
        y = PAGE_H - TOP_MARGIN - (row + 1) * CARD_H
        _draw_card(c, x, y, art, numbers.get(art.id), outline)
    c.showPage()   # flush the last (possibly partial) page
    c.save()

    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="placards-{show.slug}.pdf"'
    return resp
