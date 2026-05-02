from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from gallery.models import Artist, Artwork, Event, Show, Tag
from gallery.models.tags import ensure_open_call_tag
from gallery.permissions import is_curator_user, is_staff_user


User = get_user_model()


class UserAwareModelForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class ArtistForm(UserAwareModelForm):
    class Meta:
        model = Artist
        fields = (
            'first_name',
            'last_name',
            'email',
            'phone',
            'website',
            'instagram',
            'bio',
            'statement',
            'image',
            'tags',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not is_staff_user(self.user):
            self.fields.pop('tags')


class ArtworkForm(UserAwareModelForm):
    class Meta:
        model = Artwork
        fields = (
            'name',
            'shows',
            'artists',
            'end_year',
            'start_year',
            'medium',
            'dimensions',
            'image',
            'price',
            'pricing',
            'replacement_cost',
            'is_sold',
            'is_public',
            'open_call_available',
            'tags',
            'description',
            'installation',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not is_staff_user(self.user):
            for field_name in ('artists', 'shows', 'is_public', 'tags'):
                self.fields.pop(field_name)

    def save(self, commit=True):
        artwork = super().save(commit=commit)
        if not commit:
            return artwork

        open_call_tag = ensure_open_call_tag()
        if artwork.open_call_available:
            artwork.tags.add(open_call_tag)
        else:
            artwork.tags.remove(open_call_tag)
        return artwork


class ShowForm(UserAwareModelForm):
    artists = forms.ModelMultipleChoiceField(queryset=Artist.objects.none(), required=False)
    artworks = forms.ModelMultipleChoiceField(queryset=Artwork.objects.none(), required=False)

    class Meta:
        model = Show
        fields = (
            'name',
            'description',
            'image',
            'managing_curator',
            'is_open_call',
            'submission_deadline',
            'start',
            'end',
            'tags',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields['artists'].queryset = Artist.objects.order_by('name')
        artworks = Artwork.objects.order_by('name').distinct()
        if self._is_open_call_enabled():
            artworks = artworks.filter(Q(open_call_available=True) | Q(shows=self.instance)).distinct()
        self.fields['artworks'].queryset = artworks
        if self.instance.pk:
            self.fields['artists'].initial = self.instance.artists.all()
            self.fields['artworks'].initial = self.instance.artworks.all()
        self.fields['managing_curator'].queryset = User.objects.filter(groups__name='curator').distinct().order_by('first_name', 'last_name', 'username')
        if not is_staff_user(self.user):
            self.fields.pop('managing_curator')

    def _is_open_call_enabled(self):
        if self.is_bound:
            return self.data.get(self.add_prefix('is_open_call')) in {'on', 'true', 'True', '1'}
        return bool(self.instance.pk and self.instance.is_open_call)

    def save(self, commit=True):
        show = super().save(commit=commit)
        if not commit:
            return show

        open_call_tag = ensure_open_call_tag()
        selected_artworks = self.cleaned_data['artworks']
        selected_artist_ids = list(self.cleaned_data['artists'].values_list('id', flat=True))
        selected_artwork_artist_ids = list(selected_artworks.values_list('artists__id', flat=True))
        show.artists.set(Artist.objects.filter(id__in=selected_artist_ids + selected_artwork_artist_ids).distinct())
        show.artworks.set(selected_artworks)
        selected_artworks.update(is_public=True)
        if show.is_open_call:
            show.tags.add(open_call_tag)
        else:
            show.tags.remove(open_call_tag)
        return show


class EventForm(UserAwareModelForm):
    class Meta:
        model = Event
        fields = (
            'name',
            'description',
            'show',
            'managing_curator',
            'image',
            'date',
            'start',
            'end',
            'tags',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields['managing_curator'].queryset = User.objects.filter(groups__name='curator').distinct().order_by('first_name', 'last_name', 'username')
        if is_staff_user(self.user):
            return

        self.fields.pop('managing_curator')
        if is_curator_user(self.user):
            self.fields['show'].queryset = Show.objects.filter(managing_curator=self.user).distinct()
