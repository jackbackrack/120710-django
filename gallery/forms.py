import re

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field, Row, Column, HTML, Fieldset

from gallery.models import Artist, Artwork, ArtworkImage, ArtworkSubmission, Event, Show, Site, Tag
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
    # Plain text field (not URLField) so a scheme-less domain like "howardhersh.com"
    # is accepted; clean_website adds https:// and then validates it as a URL.
    website = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={'placeholder': 'howardhersh.com'}),
    )

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
            'zipcode': forms.TextInput(attrs={'placeholder': 'e.g. 94710', 'maxlength': '10'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not is_staff_user(self.user):
            self.fields.pop('user')
        else:
            self.fields['user'].queryset = User.objects.order_by('email')
            self.fields['user'].required = False
            self.fields['user'].label = 'Linked user account'
        for f in ('first_name', 'last_name', 'zipcode', 'image'):
            self.fields[f].required = True
        self.fields['first_name'].label = 'First name'
        self.fields['first_name'].help_text = 'Your public first name.'
        self.fields['last_name'].label = 'Last name'
        self.fields['last_name'].help_text = 'Your public last name.'
        self.fields['zipcode'].label = 'Zip code'
        self.fields['zipcode'].help_text = 'US zip code (e.g. 94710). Required to submit artwork to shows.'
        self.fields['image'].label = 'Profile photo'
        self.fields['image'].help_text = (
            'A photo of you (the artist), not your artwork. '
            'Appears on your public profile. Required to submit artwork to shows.'
        )
        self.fields['email'].required = True
        self.fields['email'].help_text = 'Used to contact you and to link your account.'

        # Group the form so it's obvious what's required: required fields (with
        # asterisks) come first under a "Required" heading, optional ones after.
        self.helper = FormHelper()
        self.helper.form_tag = False   # the template supplies <form> + submit button
        required = ['first_name', 'last_name', 'email', 'zipcode', 'image']
        optional = ['phone', 'website', 'instagram', 'venmo', 'bio', 'statement']
        layout = Layout(
            HTML('<p class="text-muted small mb-3">Fields marked '
                 '<span class="text-danger">*</span> are required.</p>'),
            Fieldset('Required', *required),
            Fieldset(
                'Optional',
                HTML('<p class="text-muted small mb-3">These help people learn more '
                     'about you and get in touch — add a phone number, your website '
                     'and social links, and a short bio or artist statement.</p>'),
                *optional,
            ),
        )
        if 'user' in self.fields:
            layout.append(Fieldset('Admin', 'user'))
        self.helper.layout = layout

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
        from django.core.validators import URLValidator
        from django.core.exceptions import ValidationError as DjangoValidationError
        value = (self.cleaned_data.get('website') or '').strip()
        if not value:
            return None
        if '://' not in value:
            value = 'https://' + value   # accept a bare domain
        try:
            URLValidator()(value)
        except DjangoValidationError:
            raise forms.ValidationError(
                'Enter a valid website, e.g. howardhersh.com or https://howardhersh.com')
        return value


class ArtworkImageForm(forms.ModelForm):
    class Meta:
        model = ArtworkImage
        fields = ['image', 'order']
        widgets = {
            'order': forms.NumberInput(attrs={'style': 'width:4em', 'min': '1'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['image'].required = False
        self.fields['order'].required = False

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if image and hasattr(image, 'size') and image.size > MAX_IMAGE_SIZE:
            raise forms.ValidationError('Image file too large — maximum size is 50 MB.')
        return image

    def has_changed(self):
        if not self.instance.pk:
            image_value = self.fields['image'].widget.value_from_datadict(
                self.data, self.files, self.add_prefix('image')
            )
            if not image_value:
                return False
        return super().has_changed()

    def clean_order(self):
        value = self.cleaned_data.get('order')
        return value if value is not None else 0


ArtworkImageFormSet = forms.inlineformset_factory(
    Artwork, ArtworkImage,
    form=ArtworkImageForm,
    extra=0,
    can_delete=True,
)


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
            'layout_image',
            'pricing_type',
            'price',
            'replacement_cost',
            'is_sold',
            'description',
            'url',
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
        self.fields['medium'].required = True
        if not (self.instance and self.instance.pk):
            self.fields['image'].required = True

        self.fields['name'].label = 'Title'
        self.fields['name'].help_text = 'Title of the artwork.'
        self.fields['end_year'].label = 'Year completed'
        self.fields['end_year'].help_text = 'Year the work was finished.'
        self.fields['start_year'].label = 'Start year'
        self.fields['start_year'].help_text = 'Only fill in if the work spans multiple years.'
        self.fields['medium'].label = 'Medium'
        self.fields['medium'].help_text = 'Materials used, e.g. oil on canvas, bronze, digital print.'
        self.fields['width_inches'].label = 'Width (in)'
        self.fields['height_inches'].label = 'Height (in)'
        self.fields['depth_inches'].label = 'Depth (in, optional)'
        # Rendered manually in the edit template next to the "crop from main image"
        # button, so its label reads as one of two ways to set the layout image.
        self.fields['layout_image'].label = 'Upload a cropped image'
        self.fields['layout_image'].help_text = ''

        for f in ('width_inches', 'height_inches', 'depth_inches'):
            self.fields[f].widget.attrs.update({'class': 'dim-input', 'step': 'any', 'min': '0'})
        self.fields['width_inches'].widget.attrs['placeholder'] = 'W'
        self.fields['height_inches'].widget.attrs['placeholder'] = 'H'
        self.fields['depth_inches'].widget.attrs['placeholder'] = 'D'

        self.helper = FormHelper()
        self.helper.form_tag = False
        dims_row = Row(
            Column(Field('width_inches'), css_class='col-auto'),
            Column(HTML('<span class="dim-sep">×</span>'), css_class='col-auto align-self-end mb-3'),
            Column(Field('height_inches'), css_class='col-auto'),
            Column(HTML('<span class="dim-sep">×</span>'), css_class='col-auto align-self-end mb-3'),
            Column(Field('depth_inches'), css_class='col-auto'),
            css_class='align-items-end g-2',
        )
        # Required fields grouped first (with asterisks), then pricing, then the
        # rest — so it's obvious what must be filled in.
        self.helper.layout = Layout(
            HTML('<p class="text-muted small mb-3">Fields marked '
                 '<span class="text-danger">*</span> are required.</p>'),
            Fieldset(
                'Required',
                'name',
                *((['artists'] if 'artists' in self.fields else [])),
                'end_year',
                'medium',
                dims_row,
                'image',
            ),
            Fieldset(
                'Pricing',
                'pricing_type',
                Field('price', wrapper_id='div_id_price'),
                'replacement_cost',
                'is_sold',
            ),
            Fieldset(
                'Additional details (optional)',
                'start_year',
                'description',
                'url',
                'installation',
            ),
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



class RoomConfigForm(forms.ModelForm):
    class Meta:
        from gallery.models.room import RoomConfig
        model = RoomConfig
        fields = ('width_in', 'depth_in', 'height_in',
                  'wall_n_image', 'wall_e_image', 'wall_s_image', 'wall_w_image',
                  'floor_image', 'ceiling_image')
        labels = {
            'width_in':      'Room width (in, E–W)',
            'depth_in':      'Room depth (in, N–S)',
            'height_in':     'Room height (in)',
            'wall_n_image':  'North wall image',
            'wall_e_image':  'East wall image',
            'wall_s_image':  'South wall image',
            'wall_w_image':  'West wall image',
            'floor_image':   'Floor image',
            'ceiling_image': 'Ceiling image',
        }
        widgets = {
            'width_in':  forms.NumberInput(attrs={'step': '1'}),
            'depth_in':  forms.NumberInput(attrs={'step': '1'}),
            'height_in': forms.NumberInput(attrs={'step': '1'}),
        }


class WallObstacleForm(forms.ModelForm):
    def has_changed(self):
        # A brand-new row (no pk) with a blank label is treated as empty and
        # skipped by the formset — so clicking "+ Add row" and not filling it in
        # never produces validation errors.
        if not self.instance.pk:
            label = (self.data.get(self.add_prefix('label')) or '').strip()
            if not label:
                return False
        return super().has_changed()

    class Meta:
        from gallery.models.room import WallObstacle
        model = WallObstacle
        fields = ('wall', 'label', 'x_in', 'y_in', 'z_in', 'w_in', 'h_in')
        labels = {
            'x_in': 'Horiz center (in)',
            'y_in': 'Height center (in)',
            'z_in': 'Depth center (in)',
            'w_in': 'Width (in)',
            'h_in': 'Height (in)',
        }
        help_texts = {
            'x_in': 'For N/S walls only. Horizontal from room center (+ = east).',
            'z_in': 'For E/W walls only. Along-wall from center (+ = south).',
            'y_in': 'Center height above the floor.',
        }
        widgets = {
            'wall':  forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'label': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Door'}),
            'x_in':  forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '1'}),
            'y_in':  forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '1'}),
            'z_in':  forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '1'}),
            'w_in':  forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '1'}),
            'h_in':  forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '1'}),
        }


def _make_obstacle_formset(**kwargs):
    from django.forms import inlineformset_factory
    from gallery.models.room import RoomConfig, WallObstacle
    return inlineformset_factory(
        RoomConfig, WallObstacle, form=WallObstacleForm,
        extra=kwargs.pop('extra', 2), can_delete=True,
    )


class SiteSupportForm(forms.ModelForm):
    """A reusable pedestal/shelf definition (catalog) for a site."""
    def has_changed(self):
        # A blank new row is skipped, like the obstacle formset.
        if not self.instance.pk:
            label = (self.data.get(self.add_prefix('label')) or '').strip()
            if not label:
                return False
        return super().has_changed()

    class Meta:
        from gallery.models.room import SiteSupport
        model = SiteSupport
        fields = ('label', 'w_in', 'h_in', 'd_in', 'texture')
        labels = {'w_in': 'Width (in)', 'h_in': 'Height (in)', 'd_in': 'Depth (in)',
                  'texture': 'Texture'}
        widgets = {
            'label': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Plinth A'}),
            'w_in':  forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.5', 'style': 'width:6em'}),
            'h_in':  forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.5', 'style': 'width:6em'}),
            'd_in':  forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.5', 'style': 'width:6em'}),
            'texture': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }


def _make_support_formset(**kwargs):
    from django.forms import inlineformset_factory
    from gallery.models.room import RoomConfig, SiteSupport
    return inlineformset_factory(
        RoomConfig, SiteSupport, form=SiteSupportForm,
        extra=kwargs.pop('extra', 2), can_delete=True,
    )


class SiteForm(UserAwareModelForm):
    class Meta:
        model = Site
        fields = (
            'name',
            'street',
            'city',
            'state',
            'postal_code',
            'country',
            'email',
            'phone',
            'instagram',
            'website',
            'description',
            'image',
            'icon',
            'status',
            'latitude',
            'longitude',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
        }


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
    sites = forms.ModelMultipleChoiceField(queryset=Site.objects.none(), required=False)

    class Meta:
        model = Show
        fields = (
            'name',
            'show_type',
            'description',
            'image',
            'status',
            'blind_review',
            'self_install',
            'submission_type',
            'max_submissions_per_artist',
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
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        # Curators are Artists; some (esp. on legacy shows) have no linked user
        # account. Allow any artist so account-less curators can be assigned and
        # are never silently dropped when the show is edited/saved.
        self.fields['curators'].queryset = Artist.objects.all().order_by('name')
        if self.instance.pk:
            self.fields['curators'].initial = self.instance.curators.all()
        self.fields['sites'].queryset = Site.objects.all().order_by('name')
        if self.instance.pk:
            self.fields['sites'].initial = self.instance.sites.all()
        self.fields['submission_deadline'].required = True
        if not is_staff_user(self.user) and not is_curator_user(self.user):
            self.fields.pop('status')
            self.fields.pop('blind_review')

    def save(self, commit=True):
        show = super().save(commit=commit)
        if not commit:
            return show
        show.curators.set(self.cleaned_data['curators'])
        show.sites.set(self.cleaned_data['sites'])
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


class ScheduleWindowForm(forms.Form):
    """Curator: add a drop-off/install or pickup window (date + time range)."""
    date  = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    start = forms.TimeField(widget=forms.TimeInput(attrs={'type': 'time', 'step': '900'}))
    end   = forms.TimeField(widget=forms.TimeInput(attrs={'type': 'time', 'step': '900'}))

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get('start'), cleaned.get('end')
        if s and e and s >= e:
            raise forms.ValidationError('Start time must be before end time.')
        return cleaned


class ArtistScheduleForm(forms.Form):
    """Artist: pick a window and a specific time within it."""
    def __init__(self, *args, windows=None, **kwargs):
        windows = list(windows or [])
        self._windows = {w.pk: w for w in windows}
        super().__init__(*args, **kwargs)
        self.fields['window'] = forms.ChoiceField(
            label='Window',
            choices=[(w.pk, '%s · %s–%s' % (
                w.date, w.start.strftime('%I:%M %p').lstrip('0'),
                w.end.strftime('%I:%M %p').lstrip('0'))) for w in windows],
        )
        self.fields['time'] = forms.TimeField(
            label='Time', widget=forms.TimeInput(attrs={'type': 'time', 'step': '900'}))

    def clean(self):
        cleaned = super().clean()
        wid, t = cleaned.get('window'), cleaned.get('time')
        if wid and t:
            w = self._windows.get(int(wid))
            if not w:
                raise forms.ValidationError('Please choose a valid window.')
            if not (w.start <= t <= w.end):
                raise forms.ValidationError('Please pick a time within the selected window.')
            cleaned['window_obj'] = w
        return cleaned
