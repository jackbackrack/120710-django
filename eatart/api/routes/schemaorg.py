from fastapi import APIRouter, Request

from gallery.models import Artist, Artwork, Event, Show

from eatart.schemaorg.mappers import artwork_to_schema, artist_to_schema, build_graph, event_to_schema, gallery_to_schema, schema_to_dict, show_to_schema


router = APIRouter(prefix='/schema', tags=['schema.org'])


@router.get('/art-gallery')
def art_gallery(request: Request):
    return schema_to_dict(gallery_to_schema(request))


@router.get('/artists')
def artists(request: Request):
    items = [artist_to_schema(artist, request) for artist in Artist.objects.order_by('name')]
    return build_graph(items)


@router.get('/artworks')
def artworks(request: Request):
    items = [artwork_to_schema(artwork, request) for artwork in Artwork.objects.order_by('name')]
    return build_graph(items)


@router.get('/shows')
def shows(request: Request):
    items = [show_to_schema(show, request) for show in Show.objects.order_by('-start')]
    return build_graph(items)


@router.get('/events')
def events(request: Request):
    items = [event_to_schema(event, request) for event in Event.objects.order_by('date', 'start')]
    return build_graph(items)
