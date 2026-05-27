from django import forms
from django.contrib.auth import get_user_model
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field, Row, Column, HTML

from gallery.models import Artist, Artwork, ArtworkSubmission, Event, Show, Tag
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
            'venmo',
            'bio',
            'statement',
            'image',
            'tags',
            'user',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not is_staff_user(self.user):
            self.fields.pop('tags')
            self.fields.pop('user')
        else:
            self.fields['user'].queryset = User.objects.order_by('email')
            self.fields['user'].required = False
            self.fields['user'].label = 'Linked user account'


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
            'width_inches',
            'height_inches',
            'depth_inches',
            'image',
            'price',
            'pricing',
            'replacement_cost',
            'is_sold',
            'tags',
            'description',
            'installation',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not is_staff_user(self.user):
            for field_name in ('artists', 'shows', 'tags'):
                self.fields.pop(field_name)

        self.fields['width_inches'].required = True
        self.fields['height_inches'].required = True
        if not (self.instance and self.instance.pk):
            self.fields['image'].required = True

        for f in ('width_inches', 'height_inches', 'depth_inches'):
            self.fields[f].widget.attrs.update({'class': 'dim-input', 'step': 'any', 'min': '0'})
        self.fields['width_inches'].widget.attrs['placeholder'] = 'W'
        self.fields['height_inches'].widget.attrs['placeholder'] = 'H'
        self.fields['depth_inches'].widget.attrs['placeholder'] = 'D'

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'name',
            *((['shows', 'artists'] if 'artists' in self.fields else [])),
            'end_year',
            'start_year',
            'medium',
            Row(
                Column(Field('width_inches'), css_class='col-auto'),
                Column(HTML('<span class="dim-sep">×</span>'), css_class='col-auto align-self-end mb-3'),
                Column(Field('height_inches'), css_class='col-auto'),
                Column(HTML('<span class="dim-sep">×</span>'), css_class='col-auto align-self-end mb-3'),
                Column(Field('depth_inches'), css_class='col-auto'),
                css_class='align-items-end g-2',
            ),
            'image',
            'price',
            'pricing',
            'replacement_cost',
            'is_sold',
            *((['tags'] if 'tags' in self.fields else [])),
            'description',
            'installation',
        )


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
    curators = forms.ModelMultipleChoiceField(queryset=Artist.objects.none(), required=False)

    class Meta:
        model = Show
        fields = (
            'name',
            'show_type',
            'location',
            'description',
            'image',
            'is_open_call',
            'submission_deadline',
            'decision_date',
            'start',
            'end',
            'tags',
        )
        widgets = {
            'submission_deadline': forms.DateInput(attrs={'type': 'date'}),
            'decision_date': forms.DateInput(attrs={'type': 'date'}),
            'start': forms.DateInput(attrs={'type': 'date'}),
            'end': forms.DateInput(attrs={'type': 'date'}),
            'location': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields['artists'].queryset = Artist.objects.order_by('name')
        self.fields['artworks'].queryset = Artwork.objects.order_by('name').distinct()
        self.fields['curators'].queryset = Artist.objects.filter(
            user__groups__name='curator'
        ).order_by('name')
        if self.instance.pk:
            self.fields['artists'].initial = self.instance.artists.all()
            self.fields['artworks'].initial = self.instance.artworks.all()
            self.fields['curators'].initial = self.instance.curators.all()

    def save(self, commit=True):
        show = super().save(commit=commit)
        if not commit:
            return show

        selected_artworks = self.cleaned_data['artworks']
        selected_artist_ids = list(self.cleaned_data['artists'].values_list('id', flat=True))
        selected_artwork_artist_ids = list(selected_artworks.values_list('artists__id', flat=True))
        show.artists.set(Artist.objects.filter(id__in=selected_artist_ids + selected_artwork_artist_ids).distinct())
        show.artworks.set(selected_artworks)
        show.curators.set(self.cleaned_data['curators'])
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
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'start': forms.TimeInput(attrs={'type': 'time'}),
            'end': forms.TimeInput(attrs={'type': 'time'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if is_curator_user(self.user):
            self.fields['show'].queryset = Show.objects.filter(is_open_call=True).distinct()
