"""Render a signed consignment agreement as a PDF, from its snapshot and nothing else.

Never written to storage. Media is world-readable (`AWS_DEFAULT_ACL = 'public-read'`,
`AWS_QUERYSTRING_AUTH = False`), and this document carries a home address and a signature, so
there must be no file for anyone to find. It is generated per request behind a permission
check, and because the snapshot is immutable the same agreement renders identically forever.

ReportLab, which is already here for placards.
"""
import datetime
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

MUTED = colors.HexColor('#666666')
RULE = colors.HexColor('#cccccc')


def _styles():
    base = getSampleStyleSheet()
    return {
        'title': ParagraphStyle('t', parent=base['Heading1'], fontSize=16, leading=20,
                                spaceAfter=2),
        'sub': ParagraphStyle('s', parent=base['Normal'], fontSize=10, leading=14,
                              textColor=MUTED, spaceAfter=14),
        'h2': ParagraphStyle('h2', parent=base['Heading2'], fontSize=11, leading=14,
                             spaceBefore=12, spaceAfter=4),
        'body': ParagraphStyle('b', parent=base['Normal'], fontSize=9.5, leading=13.5,
                               alignment=TA_LEFT, spaceAfter=4),
        'small': ParagraphStyle('sm', parent=base['Normal'], fontSize=8, leading=11,
                                textColor=MUTED),
        'cell': ParagraphStyle('c', parent=base['Normal'], fontSize=8.5, leading=11),
    }


def _money(value):
    if value in (None, ''):
        return '—'
    return f'${float(value):,.0f}' if float(value) == int(float(value)) else f'${float(value):,.2f}'


def _pct(value):
    """25.00 → "25". A contract that says 25.00% reads like a spreadsheet."""
    if value in (None, ''):
        return '—'
    text = f'{float(value):.2f}'.rstrip('0').rstrip('.')
    return text or '0'


def _date(iso):
    if not iso:
        return '—'
    try:
        return datetime.date.fromisoformat(iso[:10]).strftime('%B %-d, %Y')
    except ValueError:
        return iso


def render_consignment(consignment):
    """Bytes of the PDF for one signed (or draft) agreement."""
    snap = consignment.snapshot
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.9 * inch, bottomMargin=0.8 * inch,
        title=f'Consignment — {snap.get("show", {}).get("name", "")}',
        author=snap.get('venue', {}).get('name', ''))

    show = snap.get('show', {})
    venue = snap.get('venue', {})
    artist = snap.get('artist', {})
    custody = snap.get('custody', {})
    flow = []

    flow.append(Paragraph('Consignment Agreement', st['title']))
    subtitle = show.get('name', '')
    if venue.get('name'):
        subtitle += f' · {venue["name"]}'
    if show.get('start') and show.get('end'):
        subtitle += f' · {_date(show["start"])} – {_date(show["end"])}'
    flow.append(Paragraph(subtitle, st['sub']))

    # Parties, side by side.
    artist_lines = [artist.get('name', '')]
    street = artist.get('street', '')
    town = ' '.join(filter(None, [artist.get('city', ''), artist.get('state', ''),
                                  artist.get('zipcode', '')]))
    artist_lines += [x for x in (street, town, artist.get('email', '')) if x]
    venue_lines = [venue.get('name', '')]
    venue_town = ' '.join(filter(None, [venue.get('city', ''), venue.get('state', ''),
                                        venue.get('postal_code', '')]))
    venue_lines += [x for x in (venue.get('street', ''), venue_town) if x]

    parties = Table([[
        Paragraph('<b>Artist</b><br/>' + '<br/>'.join(artist_lines), st['cell']),
        Paragraph('<b>Gallery</b><br/>' + '<br/>'.join(venue_lines), st['cell']),
    ]], colWidths=[3.35 * inch, 3.35 * inch])
    parties.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, RULE),
    ]))
    flow.append(parties)

    # ── The schedule of works ────────────────────────────────────────────────
    rate = snap.get('commission_rate')
    flow.append(Paragraph('Schedule of works', st['h2']))
    header = ['Work', 'Price', 'Agreed value', 'If it sells']
    data = [[Paragraph(f'<b>{h}</b>', st['cell']) for h in header]]
    for row in snap.get('artworks', []):
        desc = f'<b>{row.get("title", "")}</b>'
        bits = [str(row.get('year') or ''), row.get('medium') or '',
                row.get('dimensions') or '']
        detail = ', '.join(b for b in bits if b)
        if detail:
            desc += f'<br/><font size="8" color="#666666">{detail}</font>'
        if not row.get('for_sale'):
            sale = f'<font color="#666666">{row.get("pricing") or "Not for sale"}</font>'
        else:
            sale = (f'gallery {_money(row.get("gallery_share"))}<br/>'
                    f'you {_money(row.get("artist_share"))}')
        data.append([
            Paragraph(desc, st['cell']),
            Paragraph(_money(row.get('price')), st['cell']),
            Paragraph(_money(row.get('agreed_value')), st['cell']),
            Paragraph(sale, st['cell']),
        ])
    total = snap.get('total_agreed_value') or 0
    data.append([Paragraph('<b>Total agreed value</b>', st['cell']), '',
                 Paragraph(f'<b>{_money(total)}</b>', st['cell']), ''])

    table = Table(data, colWidths=[3.1 * inch, 0.9 * inch, 1.1 * inch, 1.6 * inch],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.black),
        ('LINEBELOW', (0, 1), (-1, -2), 0.25, RULE),
        ('LINEABOVE', (0, -1), (-1, -1), 0.75, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    flow.append(table)

    flow.append(Spacer(1, 6))
    flow.append(Paragraph(
        f'Commission: <b>{_pct(rate)}%</b> of the sale price.', st['body']))
    flow.append(Paragraph(
        f'In our care from <b>{_date(custody.get("from"))}</b>. Please collect by '
        f'<b>{_date(custody.get("pickup_by"))}</b>. Our responsibility ends '
        f'{custody.get("grace_days")} days later, on <b>{_date(custody.get("to"))}</b>, '
        f'whether or not the work has been collected.', st['body']))

    # ── Terms ────────────────────────────────────────────────────────────────
    for heading, points in snap.get('terms', []):
        block = [Paragraph(heading, st['h2'])]
        block += [Paragraph(f'• {p}', st['body']) for p in points]
        flow.append(KeepTogether(block))

    # ── Signature ────────────────────────────────────────────────────────────
    flow.append(Spacer(1, 14))
    if consignment.is_signed:
        signed_on = consignment.signed_at.strftime('%B %-d, %Y at %H:%M %Z').strip()
        sig = [
            Paragraph('Signed', st['h2']),
            Paragraph(f'<b>{consignment.signed_name}</b>', st['body']),
            Paragraph(
                f'Electronically signed on {signed_on}. '
                f'Recorded from {consignment.signed_ip or "an unknown address"}. '
                f'Agreement version {consignment.version}, terms version '
                f'{snap.get("terms_version")}.', st['small']),
        ]
    else:
        sig = [Paragraph('Not signed', st['h2']),
               Paragraph('This is a draft and has not been agreed to.', st['small'])]
    flow.append(KeepTogether(sig))

    doc.build(flow)
    return buf.getvalue()
