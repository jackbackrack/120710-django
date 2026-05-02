from gallery.views.artists import (
    ArtistCreateView,
    ArtistDeleteView,
    ArtistDetailView,
    ArtistListView,
    ArtistMailChimpView,
    ArtistUpdateView,
)
from gallery.views.artworks import (
    ArtworkCreateView,
    ArtworkDeleteView,
    ArtworkDetailView,
    ArtworkListView,
    ArtworkUpdateView,
    detail,
)
from gallery.views.events import (
    EventCreateView,
    EventDeleteView,
    EventDetailView,
    EventListView,
    EventUpdateView,
)
from gallery.views.exhibitions import (
    ShowCreateView,
    ShowDeleteView,
    ShowDetailView,
    ShowInstagramView,
    ShowListView,
    ShowPlacardsView,
    ShowUpdateView,
    redirect_to_latest_show,
)
from gallery.views.open_call import ArtistOpenCallView, OpenCallDashboardView
from gallery.views.search import SearchResultsListView

__all__ = [
    'detail',
    'ArtistListView',
    'ArtistMailChimpView',
    'ArtistDetailView',
    'ArtistUpdateView',
    'ArtistDeleteView',
    'ArtistCreateView',
    'ArtworkListView',
    'ArtworkDetailView',
    'ArtworkUpdateView',
    'ArtworkDeleteView',
    'ArtworkCreateView',
    'ShowListView',
    'ShowDetailView',
    'ShowPlacardsView',
    'ShowInstagramView',
    'ShowUpdateView',
    'ShowDeleteView',
    'ShowCreateView',
    'redirect_to_latest_show',
    'OpenCallDashboardView',
    'ArtistOpenCallView',
    'EventListView',
    'EventDetailView',
    'EventUpdateView',
    'EventDeleteView',
    'EventCreateView',
    'SearchResultsListView',
]
