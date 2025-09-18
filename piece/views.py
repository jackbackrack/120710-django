from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView, DetailView
from django.views.generic.edit import UpdateView, DeleteView, CreateView
from django.urls import reverse_lazy
from django.shortcuts import render, get_object_or_404

from .models import Show, Artist, Piece, Event

def detail(request, pk):
    piece = get_object_or_404(Piece, pk=pk)
    return render(request, 'piece/piece_detail.html', {
      'piece': piece
    })

class ShowListView(ListView):
    model = Show
    template_name = "piece/show_list.html"

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

def event_detail(request, pk):
    event = get_object_or_404(Event, pk=pk)
    return render(request, 'piece/event_detail.html', {
      'event': event
    })

def show_detail(request, pk):
    show = get_object_or_404(Show, pk=pk)
    # pieces = Piece.objects.filter(shows__id = pk).order_by('artists__name', 'name')
    # pieces = Piece.objects.filter(shows__id = pk).order_by('name')
    pieces = Piece.objects.all()
    # artists = Artist.objects.filter(pieces__in = pieces).distinct().order_by('name')
    artists = Artist.objects.all()
    print(artists.count(), flush=True)
    return render(request, 'piece/show_detail.html', {
        'show': show,
        'pieces': pieces,
        'artists': artists
    })

def piece_detail(request, pk):
    piece = get_object_or_404(Piece, pk=pk)
    return render(request, 'piece/piece_detail.html', {
      'piece': piece
    })

def artist_detail(request, pk):
    artist = get_object_or_404(Artist, pk=pk)
    return render(request, 'piece/artist_detail.html', {
      'artist': artist
    })
