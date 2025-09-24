from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView, DetailView
from django.views.generic.edit import UpdateView, DeleteView, CreateView
from django.urls import reverse_lazy
from django.shortcuts import render, get_object_or_404
from django.db.models import Q

from .models import Show, Artist, Piece, Event

def detail(request, pk):
    piece = get_object_or_404(Piece, pk=pk)
    return render(request, 'piece/piece_detail.html', {
      'piece': piece
    })

class ShowListView(ListView):
    model = Show
    template_name = "piece/show_list.html"
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        shows = Show.objects.order_by('-start')
        context['shows'] = shows
        return context

class ShowDetailView(DetailView):
    model = Show
    template_name = "piece/show_detail.html"
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        show = kwargs.get('object') 
        pieces = Piece.objects.filter(shows = show).order_by('artists__name')
        artists = Artist.objects.filter(pieces__in = show.pieces.all()).distinct().order_by('name')
        context['artists'] = artists
        context['pieces'] = pieces
        return context

class ShowUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Show
    fields = (
        "name",
        "description",
        "image",
        "curators",
        "start",
        "end",
        )
    template_name = "piece/show_edit.html"

    def test_func(self):
        obj = self.get_object()
        for curator in obj.curators.all():
            if curator.user == self.request.user:
                return True
        return self.request.user.is_superuser

class ShowDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Show
    template_name = "piece/show_delete.html"
    success_url = reverse_lazy("piece:show_list")

    def test_func(self):
        obj = self.get_object()
        for curator in obj.curators.all():
            if curator.user == self.request.user:
                return True
        return self.request.user.is_superuser

class ShowCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Show
    fields = (
        "name",
        "description",
        "image",
        "curators",
        "start",
        "end",
        )
    template_name = "piece/show_new.html"

    def test_func(self):
        return self.request.user.groups.filter(name='curator').exists() or self.request.user.is_superuser

    # # TODO: need to make artist
    # def form_valid(self, form):
    #     form.instance.curators = [ self.request.user ]
    #     return super().form_valid(form)

class EventListView(ListView):
    model = Event
    template_name = "piece/event_list.html"

class EventDetailView(DetailView):
    model = Event
    template_name = "piece/event_detail.html"

class EventUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Event
    fields = (
        "name",
        "description",
        "show",
        "image",
        "date",
        "start",
        "end",
        )
    template_name = "piece/event_edit.html"

    def test_func(self):
        obj = self.get_object()
        for curator in obj.show.curators.all():
            if curator.user == self.request.user:
                return True
        return self.request.user.is_superuser

class EventDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Event
    template_name = "piece/event_delete.html"
    success_url = reverse_lazy("piece:event_list")

    def test_func(self):
        obj = self.get_object()
        for curator in obj.show.curators.all():
            if curator.user == self.request.user:
                return True
        return self.request.user.is_superuser

class EventCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Event
    fields = (
        "name",
        "description",
        "show",
        "image",
        "date",
        "start",
        "end",
        )
    template_name = "piece/event_new.html"
    
    def test_func(self):
        return self.request.user.groups.filter(name='curator').exists() or self.request.request.user.is_superuser

class ArtistListView(ListView):
    model = Artist
    template_name = "piece/artist_list.html"

class ArtistDetailView(DetailView):
    model = Artist
    template_name = "piece/artist_detail.html"

class ArtistUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Artist
    fields = (
        "name",
        "email",
        "phone",
        "website",
        "instagram",
        "bio",
        "statement",
        "image",
        )
    template_name = "piece/artist_edit.html"

    def test_func(self):
        obj = self.get_object()
        return obj.user == self.request.user or self.request.user.is_superuser

class ArtistDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Artist
    template_name = "piece/artist_delete.html"
    success_url = reverse_lazy("piece:artist_list")

    def test_func(self):
        obj = self.get_object()
        return obj.user == self.request.user or self.request.user.is_superuser

class ArtistCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Artist
    fields = (
        "name",
        "email",
        "phone",
        "website",
        "instagram",
        "bio",
        "statement",
        "image",
        )
    template_name = "piece/artist_new.html"

    def form_valid(self, form):
        form.instance.user = self.request.user
        print('user set to', self.request.user)
        return super().form_valid(form)

    def test_func(self):
        user = self.request.user
        return not user.artists.exists() and ( user.is_superuser or user.groups.filter(name='artist').exists() )

class PieceListView(ListView):
    model = Piece
    template_name = "piece/piece_list.html"

class PieceDetailView(DetailView):
    model = Piece
    template_name = "piece/piece_detail.html"

class PieceUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Piece
    fields = (
        "name",
        "shows",
        "artists",
        "end_year",
        "start_year",
        "medium",
        "dimensions",
        "image",
        "price",
        "pricing",
        "replacement_cost",
        "is_sold",
        "description",
        "installation",
        )
    template_name = "piece/piece_edit.html"

    def test_func(self):
        obj = self.get_object()
        for artist in obj.artists.all():
            if artist.user == self.request.user:
                return True
        return self.request.user.is_superuser

class PieceDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Piece
    template_name = "piece/piece_delete.html"
    success_url = reverse_lazy("piece:piece_list")

    def test_func(self):
        obj = self.get_object()
        for artist in obj.artists.all():
            if artist.user == self.request.user:
                return True
        return self.request.user.is_superuser

class PieceCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Piece
    fields = (
        "name",
        "shows",
        "artists",
        "end_year",
        "start_year",
        "medium",
        "dimensions",
        "image",
        "price",
        "pricing",
        "replacement_cost",
        "is_sold",
        "description",
        "installation",
        )
    template_name = "piece/piece_new.html"

    # todo: artist with this user id
    # def form_valid(self, form):
    #     form.instance.artists = [ self.request.user ]
    #     return super().form_valid(form)

    def test_func(self):
        return self.request.user.is_superuser or self.request.user.groups.filter(name='artist').exists()

class SearchResultsListView(ListView):
    model = Artist
    context_object_name = "artist_list"
    template_name = "piece/search_results.html"
    def get_queryset(self):
        query = self.request.GET.get("q")
        return Artist.objects.filter(Q(name__icontains=query))
    def get_context_data(self,*args,**kwargs):
       context = super(SearchResultsListView,self).get_context_data(*args,**kwargs)

       query = self.request.GET.get('q')
       pieces = Piece.objects.filter(name__icontains=query)

       context['piece_list'] = pieces
       return context

