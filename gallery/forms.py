import re

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field, Row, Column, HTML

from gallery.models import Artist, Artwork, ArtworkSubmission, Event, Show, Tag
from gallery.permissions import is_curator_user, is_staff_user


def _captcha_field():
    if getattr(settings, 'RECAPTCHA_ENABLED', False):
        from django_recaptcha.fields import ReCaptchaField
        return ReCaptchaField()
    return forms.CharField(required=False, widget=forms.HiddenInput)


class ArtworkInquiryForm(forms.Form):
    sender_name = forms.CharField(max_length=150, label='Your name',
                                  widget=forms.TextInput(attrs={'placeholder': 'Jane Doe'}))
    sender_email = forms.EmailField(label='Your email address',
                                    widget=forms.EmailInput(attrs={'placeholder': 'jane@doe.com'}))
    message = forms.CharField(widget=forms.Textarea(attrs={'rows': 5}), label='Message')
    captcha = _captcha_field()

MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50 MB


def validate_image_size(image):
    if image and hasattr(image, 'size') and image.size > MAX_IMAGE_SIZE:
        raise ValidationError(f'Image file too large — maximum size is 50 MB (got {image.size // (1024*1024)} MB).')

User = get_user_model()



class UserAwareModelForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        for field_name, value in cleaned.items():
            if hasattr(value, 'size'):
                validate_image_size(value)
        return cleaned


class ArtistForm(UserAwareModelForm):
    class Meta:
        model = Artist
        fields = (
            'first_name',
            'last_name',
            'email',
            'zipcode',
            'image',
            'phone',
            'website',
            'instagram',
            'venmo',
            'bio',
            'statement',
            'user',
        )
        widgets = {
            'phone': forms.TextInput(attrs={'type': 'tel', 'placeholder': '+1 (555) 555-5555'}),
            'zipcode': forms.TextInput(attrs={'placeholder': '94710', 'maxlength': '10'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not is_staff_user(self.user):
            self.fields.pop('user')
        else:
            self.fields['user'].queryset = User.objects.order_by('email')
            self.fields['user'].required = False
            self.fields['user'].label = 'Linked user account'

    def clean_zipcode(self):
        value = (self.cleaned_data.get('zipcode') or '').strip()
        if value and not re.match(r'^\d{5}(-\d{4})?$', value):
            raise forms.ValidationError('Enter a valid US zip code (e.g. 94710 or 94710-1234).')
        return value

    def clean_instagram(self):
        value = (self.cleaned_data.get('instagram') or '').strip()
        if value and not value.startswith('@'):
            value = '@' + value
        return value or None

    def clean_venmo(self):
        value = (self.cleaned_data.get('venmo') or '').strip()
        if value and not value.startswith('@'):
            value = '@' + value
        return value or None

    def clean_website(self):
        value = (self.cleaned_data.get('website') or '').strip()
        if value and '://' not in value:
            value = 'https://' + value
        return value or None


class ArtworkForm(UserAwareModelForm):
    class Meta:
        model = Artwork
        fields = (
            'name',
            'artists',
            'end_year',
            'start_year',
            'medium',
            'width_inches',
            'height_inches',
            'depth_inches',
            'image',
            'pricing_type',
            'price',
            'replacement_cost',
            'is_sold',
            'description',
            'installation',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        user_has_artist = (
            self.user and
            is_staff_user(self.user) and
            hasattr(self.user, 'artists') and
            self.user.artists.exists()
        )
        if not is_staff_user(self.user) or user_has_artist:
            self.fields.pop('artists')

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
            *((['artists'] if 'artists' in self.fields else [])),
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
            'pricing_type',
            Field('price', wrapper_id='div_id_price'),
            'replacement_cost',
            'is_sold',
            'description',
            'installation',
        )

    def clean(self):
        cleaned = super().clean()
        pricing_type = cleaned.get('pricing_type')
        price = cleaned.get('price')
        from gallery.models import Artwork
        if pricing_type == Artwork.PRICING_FOR_SALE and price is None:
            self.add_error('price', 'A price is required when "For Sale" is selected.')
        if pricing_type in (Artwork.PRICING_NFS, Artwork.PRICING_ON_REQUEST):
            cleaned['price'] = None
        return cleaned


class QuickArtworkForm(forms.ModelForm):
    class Meta:
        model = Artwork
        fields = ('name', 'end_year', 'start_year', 'medium', 'width_inches', 'height_inches', 'depth_inches', 'image')
        widgets = {
            'width_inches': forms.NumberInput(attrs={'placeholder': 'W', 'class': 'dim-input', 'step': 'any', 'min': '0'}),
            'height_inches': forms.NumberInput(attrs={'placeholder': 'H', 'class': 'dim-input', 'step': 'any', 'min': '0'}),
            'depth_inches': forms.NumberInput(attrs={'placeholder': 'D', 'class': 'dim-input', 'step': 'any', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['width_inches'].required = True
        self.fields['height_inches'].required = True
        self.fields['image'].required = False


class ArtworkSubmissionForm(forms.ModelForm):
    class Meta:
        model = ArtworkSubmission
        fields = ['artwork']

    def __init__(self, *args, show=None, artist=None, **kwargs):
        super().__init__(*args, **kwargs)
        already_submitted = ArtworkSubmission.objects.filter(show=show).values_list('artwork_id', flat=True)
        self.fields['artwork'].queryset = artist.artworks.exclude(pk__in=already_submitted).order_by('name')
        self.fields['artwork'].empty_label = 'Select an artwork'


class ShowForm(UserAwareModelForm):
    curators = forms.ModelMultipleChoiceField(queryset=Artist.objects.none(), required=False)

    class Meta:
        model = Show
        fields = (
            'name',
            'show_type',
            'location',
            'description',
            'image',
            'status',
            'submission_type',
            'submission_deadline',
            'review_deadline',
            'decision_date',
            'start',
            'end',
        )
        widgets = {
            'submission_deadline': forms.DateInput(attrs={'type': 'date'}),
            'review_deadline': forms.DateInput(attrs={'type': 'date'}),
            'decision_date': forms.DateInput(attrs={'type': 'date'}),
            'start': forms.DateInput(attrs={'type': 'date'}),
            'end': forms.DateInput(attrs={'type': 'date'}),
            'location': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields['curators'].queryset = Artist.objects.filter(
            user__isnull=False
        ).order_by('name')
        if self.instance.pk:
            self.fields['curators'].initial = self.instance.curators.all()
        self.fields['submission_deadline'].required = True
        if not is_staff_user(self.user) and not is_curator_user(self.user):
            self.fields.pop('status')

    def save(self, commit=True):
        show = super().save(commit=commit)
        if not commit:
            return show
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
        )
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'start': forms.TimeInput(attrs={'type': 'time', 'step': '900'}),
            'end': forms.TimeInput(attrs={'type': 'time', 'step': '900'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if is_staff_user(self.user):
            self.fields['show'].queryset = Show.objects.all().distinct()
        elif is_curator_user(self.user):
            self.fields['show'].queryset = Show.objects.filter(curators__user=self.user).distinct()
