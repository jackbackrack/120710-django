from pydantic import BaseModel, ConfigDict, Field


class SchemaModel(BaseModel):
    context: str = Field(default='https://schema.org', alias='@context')
    id: str | None = Field(default=None, alias='@id')
    type: str = Field(alias='@type')

    model_config = ConfigDict(populate_by_name=True)


class PostalAddress(SchemaModel):
    type: str = Field(default='PostalAddress', alias='@type')
    streetAddress: str
    addressLocality: str
    addressRegion: str
    postalCode: str
    addressCountry: str


class PlaceReference(SchemaModel):
    type: str = Field(default='Place', alias='@type')
    name: str
    address: PostalAddress | None = None
    url: str | None = None


class EventReference(SchemaModel):
    type: str = Field(default='VisualArtsEvent', alias='@type')
    name: str
    url: str | None = None


class Person(SchemaModel):
    type: str = Field(default='Person', alias='@type')
    name: str
    givenName: str | None = None
    familyName: str | None = None
    email: str | None = None
    telephone: str | None = None
    url: str | None = None
    image: str | None = None
    sameAs: list[str] | None = None
    description: str | None = None
    worksFor: PlaceReference | None = None


class VisualArtwork(SchemaModel):
    type: str = Field(default='VisualArtwork', alias='@type')
    name: str
    url: str | None = None
    image: str | None = None
    description: str | None = None
    artist: list[Person] | None = None
    artMedium: str | None = None
    size: str | None = None
    dateCreated: str | None = None
    keywords: list[str] | None = None


class VisualArtsEvent(SchemaModel):
    type: str = Field(default='VisualArtsEvent', alias='@type')
    name: str
    url: str | None = None
    image: str | None = None
    description: str | None = None
    startDate: str | None = None
    endDate: str | None = None
    location: PlaceReference | None = None
    organizer: PlaceReference | None = None
    performer: list[Person] | None = None
    workFeatured: list[VisualArtwork] | None = None
    superEvent: EventReference | None = None
    eventAttendanceMode: str = 'https://schema.org/OfflineEventAttendanceMode'
    eventStatus: str = 'https://schema.org/EventScheduled'
    isAccessibleForFree: bool | None = None


class ArtGallery(SchemaModel):
    type: str = Field(default='ArtGallery', alias='@type')
    name: str
    legalName: str | None = None
    description: str | None = None
    url: str | None = None
    email: str | None = None
    telephone: str | None = None
    address: PostalAddress | None = None
    openingHours: list[str] | None = None
    sameAs: list[str] | None = None
    publicAccess: bool | None = None
    isAccessibleForFree: bool | None = None
