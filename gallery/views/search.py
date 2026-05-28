from django.db.models import Q, CharField
from django.db.models.functions import Cast
from django.views.generic import ListView

from gallery.models import Artist, Artwork, Show, Tag
from gallery.permissions import (
    can_manage_artist,
    can_manage_artwork,
    can_manage_show,
    can_see_all_shows,
    tag_filter_queryset,
    visible_artist_queryset,
    visible_artwork_queryset,
)


class SearchResultsListView(ListView):
    model = Artist
    context_object_name = 'artist_list'
    template_name = 'gallery/search_results.html'

    def get_queryset(self):
        query = self.request.GET.get('q', '')
        queryset = Artist.objects.filter(
            visible_artist_queryset(self.request.user),
            Q(name__icontains=query) | Q(bio__icontains=query) | Q(statement__icontains=query),
        )
        return tag_filter_queryset(queryset, self.request.GET.get('tag')).distinct()

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        query = self.request.GET.get('q', '')
        user = self.request.user
        tag = self.request.GET.get('tag')

        artists = list(context['artist_list'])
        context['can_manage_artist'] = {a.id for a in artists if can_manage_artist(user, a)}

        artwork_qs = (
            Artwork.objects
            .annotate(end_year_str=Cast('end_year', output_field=CharField()))
            .filter(
                visible_artwork_queryset(user),
                Q(name__icontains=query)
                | Q(medium__icontains=query)
                | Q(description__icontains=query)
                | Q(end_year_str__icontains=query)
                | Q(artists__name__icontains=query),
            )
            .prefetch_related('artists', 'shows')
            .distinct()
        )
        artworks = list(tag_filter_queryset(artwork_qs, tag).distinct())
        context['artwork_list'] = artworks
        context['can_manage_artwork'] = {
            a.id for a in artworks if can_manage_artwork(user, a)
        }

        show_qs = (
            Show.objects
            .filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(curators__name__icontains=query)
                | Q(start__icontains=query)
                | Q(end__icontains=query),
            )
            .prefetch_related('curators', 'tags')
        )
        if not can_see_all_shows(user):
            show_qs = show_qs.filter(status__in=Show.PUBLIC_STATUSES)
        shows = list(tag_filter_queryset(show_qs, tag).distinct())
        context['show_list'] = shows
        context['can_manage_show'] = {s.id for s in shows if can_manage_show(user, s)}

        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
        context['query'] = query
        return context
