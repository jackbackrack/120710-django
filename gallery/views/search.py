from django.db.models import Q
from django.views.generic import ListView

from gallery.models import Artist, Artwork, Tag
from gallery.permissions import tag_filter_queryset, visible_artwork_queryset


class SearchResultsListView(ListView):
    model = Artist
    context_object_name = 'artist_list'
    template_name = 'gallery/search_results.html'

    def get_queryset(self):
        query = self.request.GET.get('q', '')
        queryset = Artist.objects.filter(Q(name__icontains=query))
        return tag_filter_queryset(queryset, self.request.GET.get('tag')).distinct()

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        query = self.request.GET.get('q', '')
        artwork_queryset = Artwork.objects.filter(name__icontains=query).filter(visible_artwork_queryset(self.request.user)).distinct()
        context['artwork_list'] = tag_filter_queryset(artwork_queryset, self.request.GET.get('tag')).distinct()
        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
        context['query'] = query
        return context
