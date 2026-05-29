import datetime
import json

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from gallery.models import Show
from gallery.models.show_artwork_numbers import ShowArtworkNumber


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
