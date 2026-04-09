import json
from typing import Any

from eatart.schemaorg.profile import GALLERY_PROFILE
from eatart.schemaorg.types import ArtGallery, EventReference, Person, PlaceReference, PostalAddress, VisualArtwork, VisualArtsEvent


def build_absolute_url(request: Any, path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith('http://') or path.startswith('https://'):
        return path
    if hasattr(request, 'build_absolute_uri'):
        return request.build_absolute_uri(path)
    return str(request.base_url).rstrip('/') + path


def build_absolute_media_url(request: Any, field_file) -> str | None:
    if not field_file:
        return None
    return build_absolute_url(request, field_file.url)


def _strip_nested_context(value, *, include_context: bool):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key == '@context' and not include_context:
                continue
            cleaned[key] = _strip_nested_context(item, include_context=False)
        return cleaned
    if isinstance(value, list):
        return [_strip_nested_context(item, include_context=False) for item in value]
    return value


def schema_to_dict(item) -> dict:
    return _strip_nested_context(item.model_dump(by_alias=True, exclude_none=True), include_context=True)


def dump_json_ld(data: dict) -> str:
    return json.dumps(data, ensure_ascii=True)


def gallery_address() -> PostalAddress:
    return PostalAddress(
        streetAddress=GALLERY_PROFILE['street_address'],
        addressLocality=GALLERY_PROFILE['address_locality'],
        addressRegion=GALLERY_PROFILE['address_region'],
        postalCode=GALLERY_PROFILE['postal_code'],
        addressCountry=GALLERY_PROFILE['address_country'],
    )


def gallery_place() -> PlaceReference:
    return PlaceReference(
        id=GALLERY_PROFILE['url'],
        name=GALLERY_PROFILE['name'],
        url=GALLERY_PROFILE['url'],
        address=gallery_address(),
    )


def gallery_to_schema(request) -> ArtGallery:
    return ArtGallery(
        id=GALLERY_PROFILE['url'],
        name=GALLERY_PROFILE['name'],
        legalName=GALLERY_PROFILE['legal_name'],
        description=GALLERY_PROFILE['description'],
        url=GALLERY_PROFILE['url'],
        email=GALLERY_PROFILE['email'],
        telephone=GALLERY_PROFILE['telephone'],
        address=gallery_address(),
        openingHours=GALLERY_PROFILE['opening_hours'],
        sameAs=GALLERY_PROFILE['same_as'],
        publicAccess=GALLERY_PROFILE['public_access'],
        isAccessibleForFree=GALLERY_PROFILE['is_accessible_for_free'],
    )


def artist_to_schema(artist, request) -> Person:
    public_url = build_absolute_url(request, artist.get_absolute_url())
    same_as = [artist.website] if artist.website else []
    if artist.instagram:
        handle = artist.instagram.lstrip('@')
        same_as.append(f'https://www.instagram.com/{handle}/')

    return Person(
        id=public_url,
        name=artist.full_name,
        givenName=artist.first_name or None,
        familyName=artist.last_name or None,
        email=artist.email or None,
        telephone=artist.phone or None,
        url=public_url,
        image=build_absolute_media_url(request, artist.image),
        sameAs=same_as or None,
        description=artist.bio or artist.statement,
        worksFor=gallery_place(),
    )


def artwork_to_schema(artwork, request) -> VisualArtwork:
    public_url = build_absolute_url(request, artwork.get_absolute_url())
    artists = [artist_to_schema(artist, request) for artist in artwork.artists.all()]
    keywords = [show.name for show in artwork.shows.all()]

    if artwork.start_year and artwork.start_year != artwork.end_year:
        created = f'{artwork.start_year}/{artwork.end_year}'
    else:
        created = str(artwork.end_year) if artwork.end_year else None

    return VisualArtwork(
        id=public_url,
        name=artwork.name,
        url=public_url,
        image=build_absolute_media_url(request, artwork.image),
        description=artwork.description,
        artist=artists or None,
        artMedium=artwork.medium,
        size=artwork.dimensions,
        dateCreated=created,
        keywords=keywords or None,
    )


def show_to_schema(show, request) -> VisualArtsEvent:
    public_url = build_absolute_url(request, show.get_absolute_url())
    performers = [artist_to_schema(curator, request) for curator in show.curators.all()]
    works = [artwork_to_schema(artwork, request) for artwork in show.artworks.all()]

    return VisualArtsEvent(
        id=public_url,
        name=show.name,
        url=public_url,
        image=build_absolute_media_url(request, show.image),
        description=show.description,
        startDate=show.start.isoformat() if show.start else None,
        endDate=show.end.isoformat() if show.end else None,
        location=gallery_place(),
        organizer=gallery_place(),
        performer=performers or None,
        workFeatured=works or None,
        isAccessibleForFree=GALLERY_PROFILE['is_accessible_for_free'],
    )


def event_to_schema(event, request) -> VisualArtsEvent:
    public_url = build_absolute_url(request, event.get_absolute_url())
    start_date = None
    end_date = None
    if event.date and event.start:
        start_date = f'{event.date.isoformat()}T{event.start.isoformat()}'
    if event.date and event.end:
        end_date = f'{event.date.isoformat()}T{event.end.isoformat()}'

    return VisualArtsEvent(
        id=public_url,
        name=event.name,
        url=public_url,
        image=build_absolute_media_url(request, event.image),
        description=event.description,
        startDate=start_date,
        endDate=end_date,
        location=gallery_place(),
        organizer=gallery_place(),
        performer=[artist_to_schema(curator, request) for curator in event.show.curators.all()] or None,
        workFeatured=[artwork_to_schema(artwork, request) for artwork in event.show.artworks.all()] or None,
        superEvent=EventReference(
            id=build_absolute_url(request, event.show.get_absolute_url()),
            name=event.show.name,
            url=build_absolute_url(request, event.show.get_absolute_url()),
        ),
        isAccessibleForFree=GALLERY_PROFILE['is_accessible_for_free'],
    )


def build_graph(items):
    return {
        '@context': 'https://schema.org',
        '@graph': [_strip_nested_context(item.model_dump(by_alias=True, exclude_none=True), include_context=False) for item in items],
    }
