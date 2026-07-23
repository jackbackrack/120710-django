import datetime
import io
import json
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
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
MIN_SCALE = 0.45           # smallest fraction of base size before we truncate
QR_SIZE = 0.8 * inch       # QR square drawn on the right of the card


def _register_fonts():
    """Embed DejaVu Sans (broad Unicode coverage) so accented/greek/cyrillic/symbol
    characters render. Falls back to Helvetica if the bundled fonts are missing."""
    font_dir = os.path.join(settings.BASE_DIR, 'gallery', 'fonts')
    variants = [
        ('DejaVuSans', 'DejaVuSans.ttf'),
        ('DejaVuSans-Bold', 'DejaVuSans-Bold.ttf'),
        ('DejaVuSans-Oblique', 'DejaVuSans-Oblique.ttf'),
    ]
    try:
        for name, fname in variants:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, os.path.join(font_dir, fname)))
        return 'DejaVuSans', 'DejaVuSans-Bold', 'DejaVuSans-Oblique'
    except Exception:   # noqa: BLE001 — never break PDF generation over fonts
        return 'Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique'


_FONT, _BOLD, _ITALIC = _register_fonts()


def _draw_qr(c, url, x, y, size):
    """Draw a QR code (scannable link to the artwork) in a size x size box."""
    widget = qr.QrCodeWidget(url)
    b = widget.getBounds()
    bw, bh = (b[2] - b[0]) or 1, (b[3] - b[1]) or 1
    d = Drawing(size, size, transform=[size / bw, 0, 0, size / bh, 0, 0])
    d.add(widget)
    renderPDF.draw(d, c, x, y)


def _card_fields(artwork):
    """Placard fields (text, font, base_size, max_lines): title, year(s), artist(s),
    medium, dimensions. Title and medium may wrap to 2 lines; the rest are 1 line."""
    artists = ', '.join(str(a) for a in artwork.artists.all())
    sy, ey = artwork.start_year, artwork.end_year
    if ey and sy and sy != ey:
        years = f'{sy}–{ey}'
    else:
        years = str(ey or sy or '')
    medium = (artwork.medium or '').strip()
    rows = [
        (artwork.name or 'Untitled', _BOLD, 12.0, 2),
        (years, _FONT, 9.0, 1),
        (artists, _FONT, 10.0, 2),
        (medium, _ITALIC, 9.0, 2),
        (artwork.placard_dimensions, _FONT, 9.0, 1),
    ]
    return [(t, f, s, m) for (t, f, s, m) in rows if t]


def _ellipsize(text, font, size, max_w, force=False):
    """Trim text (adding …) until it fits max_w. force=True always adds the ellipsis."""
    if not force and stringWidth(text, font, size) <= max_w:
        return text
    ell = '…'
    t = text
    while t and stringWidth(t + ell, font, size) > max_w:
        t = t[:-1]
    return (t.rstrip() + ell) if t else ell


def _wrap(text, font, size, max_w, max_lines):
    """Word-wrap text to <= max_lines lines within max_w; the last line is
    ellipsized if content is cut off (or a single word is too long)."""
    words = text.split()
    if not words:
        return []
    lines, cur = [], words[0]
    for word in words[1:]:
        trial = cur + ' ' + word
        if stringWidth(trial, font, size) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    lines = [_ellipsize(ln, font, size, max_w) for ln in lines]   # break over-long single words
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _ellipsize(lines[-1], font, size, max_w, force=True)
    return lines


def _layout_card(artwork, avail_w, avail_h):
    """Wrap + shrink the fields to fit the card. Returns [(text, font, size)].
    Shrinks the whole block until it fits height; if it still overflows at the
    minimum size, trailing lines are dropped so nothing spills off the card."""
    fields = _card_fields(artwork)
    scale, rendered, total = 1.0, [], 0.0
    for _ in range(24):
        rendered = []
        for text, font, base, max_lines in fields:
            size = base * scale
            for ln in _wrap(text, font, size, avail_w, max_lines):
                rendered.append((ln, font, size))
        total = sum(s * LEADING for _, _, s in rendered)
        if total <= avail_h or scale <= MIN_SCALE:
            break
        scale *= 0.92
    if total > avail_h:   # still too tall at min size → hard-truncate from the bottom
        fit, h = [], 0.0
        for ln, font, size in rendered:
            if h + size * LEADING > avail_h:
                break
            fit.append((ln, font, size))
            h += size * LEADING
        rendered = fit
    return rendered


def _draw_card(c, x, y, artwork, outline, qr_url=None):
    """Draw one placard with bottom-left corner at (x, y). If qr_url is given, a QR
    code is drawn at the right and the text is confined to the left of it."""
    if outline:
        c.saveState()
        c.setStrokeGray(0.75)
        c.setLineWidth(0.5)
        c.rect(x, y, CARD_W, CARD_H)
        c.restoreState()

    text_right = x + CARD_W - PAD
    if qr_url:
        _draw_qr(c, qr_url, x + CARD_W - PAD - QR_SIZE, y + (CARD_H - QR_SIZE) / 2, QR_SIZE)
        text_right = x + CARD_W - QR_SIZE - 2 * PAD   # keep text clear of the QR

    text_left = x + PAD
    avail_w = text_right - text_left
    avail_h = CARD_H - 2 * PAD
    lines = _layout_card(artwork, avail_w, avail_h)
    block_h = sum(s * LEADING for _, _, s in lines)
    cursor = y + CARD_H / 2 + block_h / 2      # top of the vertically-centred block
    c.setFillGray(0)
    for text, font, size in lines:
        cursor -= size * LEADING
        c.setFont(font, size)
        c.drawString(text_left, cursor + size * 0.22, text)   # left-justified


@login_required
def placard_sheet_pdf(request, slug):
    """Download a PDF of the show's placards laid out for Avery 5376 sheets.
    ?outlines=1 draws faint card borders for a plain-paper alignment test."""
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404

    outline = request.GET.get('outlines') == '1'
    want_qr = request.GET.get('qr') != '0'   # QR on by default; ?qr=0 to omit
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
        qr_url = request.build_absolute_uri(art.get_absolute_url()) if want_qr else None
        _draw_card(c, x, y, art, outline, qr_url)
    c.showPage()   # flush the last (possibly partial) page
    c.save()

    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="placards-{show.slug}.pdf"'
    return resp
