from django import forms

from gallery.models import Artist, Artwork, ArtworkSubmission, Event, Show, Tag
from gallery.models.tags import ensure_open_call_tag
from gallery.permissions import is_curator_user, is_staff_user



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
            'is_public',
            'tags',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not is_staff_user(self.user):
            self.fields.pop('tags')
            self.fields.pop('is_public')


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
            'tags',
            'description',
            'installation',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not is_staff_user(self.user):
            for field_name in ('artists', 'shows', 'is_public', 'tags'):
                self.fields.pop(field_name)


class ArtworkSubmissionForm(forms.ModelForm):
    class Meta:
        model = ArtworkSubmission
        fields = ['artwork', 'statement']
        widgets = {
            'statement': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Optional artist statement for this submission',
            }),
        }
        labels = {
            'statement': 'Artist statement (optional)',
        }

    def __init__(self, *args, show=None, artist=None, **kwargs):
        super().__init__(*args, **kwargs)
        already_submitted = ArtworkSubmission.objects.filter(show=show).values_list('artwork_id', flat=True)
        self.fields['artwork'].queryset = artist.artworks.exclude(pk__in=already_submitted).order_by('name')
        self.fields['artwork'].empty_label = 'Select an artwork'
        self.fields['statement'].required = False


class ShowForm(UserAwareModelForm):
    artists = forms.ModelMultipleChoiceField(queryset=Artist.objects.none(), required=False)
    artworks = forms.ModelMultipleChoiceField(queryset=Artwork.objects.none(), required=False)

    class Meta:
        model = Show
        fields = (
            'name',
            'description',
            'image',
            'is_open_call',
            'submission_deadline',
            'decision_date',
            'start',
            'end',
            'tags',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields['artists'].queryset = Artist.objects.order_by('name')
        artworks = Artwork.objects.order_by('name').distinct()
        self.fields['artworks'].queryset = artworks
        if self.instance.pk:
            self.fields['artists'].initial = self.instance.artists.all()
            self.fields['artworks'].initial = self.instance.artworks.all()

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
            'image',
            'date',
            'start',
            'end',
            'tags',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if is_curator_user(self.user):
            self.fields['show'].queryset = Show.objects.filter(is_open_call=True).distinct()
