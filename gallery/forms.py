import re

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field, Row, Column, HTML, Fieldset

from gallery.models import (Artist, Artwork, ArtworkImage, ArtworkSubmission, Event, Show,
                            Site, Subscriber, Subscription, Tag)
from gallery import us_states
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


class NoClearFileInput(forms.ClearableFileInput):
    """A file input with no "Clear" checkbox — for images that should always have a
    value (the artist profile photo, the artwork's main image). It still shows the
    current file and lets you upload or drag-and-drop a replacement; you just can't
    blank it out (which was confusing, since these images are required)."""
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        # The default template hides the clear checkbox when the widget is "required".
        context['widget']['required'] = True
        return context


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
            'country',
            'zipcode',
            'image',
            'street',
            'city',
            'state',
            'phone',
            'website',
            'instagram',
            'venmo',
            'bio',
            'statement',
            'is_represented',
            'representing_gallery',
            'user',
        )
        widgets = {
            'phone': forms.TextInput(attrs={'type': 'tel', 'placeholder': '+1 (555) 555-5555'}),
            'zipcode': forms.TextInput(attrs={'placeholder': 'e.g. 94710', 'maxlength': '10'}),
            # accept/capture let a phone open the camera straight to a selfie —
            # the cheapest way for an artist to satisfy the photo requirement.
            'image': NoClearFileInput(attrs={'accept': 'image/*', 'capture': 'user'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        # Staff only, deliberately not curators. A non-staff curator is by definition
        # somebody whose own artist profile curates a show, so they always have one, and
        # the create view therefore never guesses on their behalf. Showing them the field
        # would buy nothing and would put every user's email address, and the ability to
        # hand a profile to any account, in front of them.
        if not is_staff_user(self.user):
            self.fields.pop('user')
        else:
            self.fields['user'].queryset = User.objects.order_by('email')
            self.fields['user'].required = False
            self.fields['user'].label = 'Linked user account'
            self.fields['user'].help_text = (
                'Leave blank for an artist who has no account and is not going to get one. '
                'Set it only to hand them the profile — whoever is chosen here can edit it '
                'and it appears as theirs.')
        # The photo IS required before submitting. Chasing photos after acceptance costs the
        # gallery far more than it costs an artist to supply one now, so the cost is paid up
        # front — and the flow works to make it cheap: the field takes a phone camera shot
        # directly, and the show page names the requirement before anyone starts rather than
        # bouncing them mid-submission.
        #
        # Google pictures are deliberately not imported to fill this. An account with no photo
        # of its own still has one — a monogram — which met the requirement without producing a
        # photograph.
        for f in ('first_name', 'last_name', 'zipcode', 'image'):
            self.fields[f].required = True
        self.fields['first_name'].label = 'First name'
        self.fields['first_name'].help_text = 'Your public first name.'
        self.fields['last_name'].label = 'Last name'
        self.fields['last_name'].help_text = 'Your public last name.'
        self.fields['zipcode'].label = 'ZIP / postal code'
        self.fields['zipcode'].help_text = (
            'ZIP code in the US, postal code elsewhere. Required to submit artwork, and '
            'used with your country to work out whether you are in the area for shows '
            'limited to one.')
        self.fields['image'].label = 'Profile photo'
        self.fields['image'].help_text = (
            'A photo of you, not your artwork — it appears on your profile and in the '
            'printed show catalogue. A phone snapshot is fine; on a phone the button '
            'opens your camera. A plain colour or a letter-on-a-circle placeholder will '
            'not be accepted.'
        )
        self.fields['email'].required = True
        self.fields['email'].help_text = 'Used to contact you and to link your account.'
        self.fields['country'].help_text = (
            'Where you are based. Some shows are open only to artists in the '
            'gallery\u2019s area, so this and your postal code decide whether you are '
            'eligible.')
        for name in ('street', 'city', 'state'):
            self.fields[name].help_text = (
                'Only needed if we consign work from you \u2014 it is how we return '
                'unsold pieces. Never shown publicly.')
        # Completion for US states, and normalisation on the way in. Free text produced
        # "c", "b" and "ca" in this table, none of which resolves to anything.
        self.fields['state'].widget = us_states.USStateInput()

        # Three states, not two. The model field is nullable so that "never asked" stays
        # distinct from "said no" \u2014 every artist who existed before this was added is in
        # the first state, and only the second is safe to consign against. Django renders a
        # NullBooleanField as a select whose empty option reads "Unknown", which sounds like
        # a fault; it says so plainly instead.
        self.fields['is_represented'].widget.choices = [
            ('unknown', 'Not answered'), ('true', 'Yes'), ('false', 'No')]
        self.fields['is_represented'].label = 'Does a gallery represent you?'
        self.fields['representing_gallery'].label = 'Which gallery?'
        self.fields['representing_gallery'].help_text = 'Only if you answered yes.'

        # Group the form so it's obvious what's required: required fields (with
        # asterisks) come first under a "Required" heading, optional ones after.
        self.helper = FormHelper()
        self.helper.form_tag = False   # the template supplies <form> + submit button
        # Country sits with the postal code: together they are what decides whether a
        # submission is inside a site's area, so they belong in the required group rather
        # than among the optional contact details.
        # Country and postal code are required: together they decide whether an artist
        # is inside a site's area, which is the whole reason the address exists here.
        # Street/city/state are optional on purpose — they are needed to *return* work,
        # which only matters for artists actually consigned from, so the consignment flow
        # is where they get asked. Requiring them at submission would collect home
        # addresses from every entrant to an open call and use almost none of them.
        # Opt-in, unchecked by default, and never pre-ticked. Consent has to be given
        # rather than not-withdrawn — that is what makes it consent, and a pre-ticked box
        # is specifically what GDPR rules out. Not a model field: the subscription lives in
        # Subscriber, and copying it onto Artist would make two things to keep in step.
        self.fields['subscribe_to_mailing_list'] = forms.BooleanField(
            required=False,
            label='Email me about open calls, shows and events',
            help_text='Occasional. Every email has a one-click unsubscribe.')
        if self.instance and self.instance.pk and self.instance.email:
            self.fields['subscribe_to_mailing_list'].initial = Subscription.objects.filter(
                subscriber__email=self.instance.email.lower(),
                site=Subscriber.default_site(), is_subscribed=True).exists()

        required = ['first_name', 'last_name', 'email', 'country', 'zipcode', 'image']
        optional = ['street', 'city', 'state', 'phone', 'website', 'instagram',
                    'venmo', 'bio', 'statement']
        representation = ['is_represented', 'representing_gallery']
        layout = Layout(
            HTML('<p class="text-muted small mb-3">Fields marked '
                 '<span class="text-danger">*</span> are required.</p>'),
            Fieldset('Required', *required),
            Fieldset(
                'Optional',
                HTML('<p class="text-muted small mb-3">Your bio and statement are '
                     'printed in the show catalogue — worth adding now while you are '
                     'here. The rest help people get in touch.</p>'),
                *optional,
            ),
            # Its own group, and worded as a fact about who we contract with rather than
            # as an eligibility question. It decides whether an artist may sign a
            # consignment agreement at all — under exclusive representation their gallery
            # holds sole authority to consign — so it is asked plainly and not buried
            # among the contact details.
            Fieldset(
                'Gallery representation',
                HTML('<p class="text-muted small mb-3">If a gallery represents you we '
                     'arrange consignment with them rather than with you. It does not '
                     'affect whether you can show here.</p>'),
                *representation,
            ),
            Fieldset('Mailing list', 'subscribe_to_mailing_list'),
        )
        if 'user' in self.fields:
            # Named for what the field does, not for who sees it: curators see it too now,
            # and "Admin" told the person nothing about the one decision it asks for.
            layout.append(Fieldset('Whose profile this is', 'user'))
        self.helper.layout = layout

    def save(self, commit=True):
        """Apply the mailing-list choice alongside the profile.

        Both directions: ticking subscribes, unticking unsubscribes. Unticking is a genuine
        withdrawal of consent and has to be honoured here, not just ignored as "no change".
        """
        artist = super().save(commit=commit)
        if commit and artist.email:
            wants = self.cleaned_data.get('subscribe_to_mailing_list')
            site = Subscriber.default_site()
            if wants:
                # Recorded, not left to the artist-directory match alone. That match is on
                # the address, so it stops holding the moment the profile's email changes
                # or the profile goes away — and somebody who joined the list from an
                # artist profile is an artist whatever happens to the row afterwards.
                # opt_in only ever adds, so this cannot clear anything they chose earlier.
                Subscriber.opt_in(
                    email=artist.email, sites=[site],
                    first_name=artist.first_name, last_name=artist.last_name,
                    source=Subscription.SOURCE_ARTIST_PROFILE,
                    interests=[Subscriber.ARTIST])
            else:
                existing = Subscription.objects.filter(
                    subscriber__email=artist.email.lower(), site=site,
                    is_subscribed=True).first()
                if existing:
                    existing.unsubscribe()
        return artist

    def clean_image(self):
        """A photograph, not a coloured square with a letter on it.

        The requirement exists so nobody is chased for a photo after acceptance. A placeholder
        costs exactly the same chasing, except the form said it was fine.

        Flatness is all this measures — it cannot tell whether the photograph is of the right
        person, and nothing short of asking can.
        """
        image = self.cleaned_data.get('image')
        # Only a freshly chosen file: an existing image comes back as a FieldFile and re-checking
        # it would make an unrelated edit fail on a photo that is already accepted.
        if image and hasattr(image, 'file') and not hasattr(image, 'instance'):
            from gallery.photos import looks_like_placeholder
            if looks_like_placeholder(image):
                raise ValidationError(
                    'That looks like a placeholder rather than a photograph — a plain colour, or '
                    'the letter-on-a-circle picture Google uses when an account has no photo. '
                    'Please upload a photo of yourself; it is printed beside your work in the '
                    'show catalogue.')
        return image

    def clean_zipcode(self):
        value = (self.cleaned_data.get('zipcode') or '').strip()
        # Only US addresses get the US format check. This used to be unconditional, which
        # meant an artist outside the US could not save a profile — and since a zip code
        # is required before submitting, could not submit at all.
        country = self.cleaned_data.get('country') or self.data.get('country')
        if value and str(country) == 'US' and not re.match(r'^\d{5}(-\d{4})?$', value):
            raise forms.ValidationError(
                'Enter a valid US ZIP code (e.g. 94710 or 94710-1234).')
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

    def clean(self):
        cleaned = super().clean()
        # Validated here rather than in clean_state, because whether a state is required to
        # be a US one depends on the country field, and a per-field clean cannot see it.
        try:
            cleaned['state'] = us_states.clean_state(cleaned.get('state'),
                                                     cleaned.get('country'))
        except ValidationError as exc:
            self.add_error('state', exc)
        return cleaned

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
            'framed_width_inches',
            'framed_height_inches',
            'framed_depth_inches',
            'hang_drop_inches',
            'image',
            'layout_image',
            'pricing_type',
            'price',
            'agreed_value',
            'is_sold',
            'description',
            'url',
            'installation',
        )
        widgets = {
            'image': NoClearFileInput(),   # required image → no confusing "Clear" checkbox
        }

    def __init__(self, *args, user=None, without_artists=False, **kwargs):
        """`without_artists` is for a caller that has already decided the attribution.

        It has to be told here rather than popped afterwards: the crispy layout below
        names the field only when it is present, and that is read once, at the end of
        this method. A caller popping it later leaves the layout still asking for a field
        the form no longer has, which crispy answers by logging a traceback per render
        and quietly dropping it — right output, alarming logs, and one library version
        away from a 500.
        """
        super().__init__(*args, user=user, **kwargs)
        user_has_artist = (
            self.user and
            is_staff_user(self.user) and
            hasattr(self.user, 'artists') and
            self.user.artists.exists()
        )
        if without_artists or not is_staff_user(self.user) or user_has_artist:
            self.fields.pop('artists', None)
        else:
            self.fields['artists'].help_text = (
                'Click to select an artist. Hold Ctrl (Windows) or ⌘ Cmd (Mac) and '
                'click to select more than one — or to remove one, Ctrl/⌘-click a '
                'highlighted name to deselect it.'
            )

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

        self.fields['framed_width_inches'].label = 'Framed width (in)'
        self.fields['framed_height_inches'].label = 'Framed height (in)'
        self.fields['framed_depth_inches'].label = 'Framed depth (in)'

        for f in ('width_inches', 'height_inches', 'depth_inches',
                  'framed_width_inches', 'framed_height_inches', 'framed_depth_inches'):
            self.fields[f].widget.attrs.update({'class': 'dim-input', 'step': 'any', 'min': '0'})
        self.fields['width_inches'].widget.attrs['placeholder'] = 'W'
        self.fields['height_inches'].widget.attrs['placeholder'] = 'H'
        self.fields['depth_inches'].widget.attrs['placeholder'] = 'D'
        self.fields['framed_width_inches'].widget.attrs['placeholder'] = 'W'
        self.fields['framed_height_inches'].widget.attrs['placeholder'] = 'H'
        self.fields['framed_depth_inches'].widget.attrs['placeholder'] = 'D'

        self.fields['hang_drop_inches'].label = 'Hang drop (in, optional)'
        self.fields['hang_drop_inches'].widget.attrs.update({'step': 'any', 'min': '0'})

        # This figure is what the gallery guarantees to pay you if the piece is lost or
        # damaged while in its care, so it is yours to set — the old wording ("what it
        # would cost to remake this piece") invited artists to state their materials cost
        # and under-value their own work.
        # "Agreed value", matching the consignment agreement exactly. Two names for one
        # number is how an artist ends up believing they are different things.
        self.fields['agreed_value'].label = 'Agreed value (optional)'
        self.fields['agreed_value'].help_text = (
            'What this piece is worth to you if it were lost, stolen or damaged beyond '
            'repair. Leave it blank and we use your asking price, which is usually right. '
            'It is what we pay you, in full, while the work is in our care. It cannot be '
            'more than the asking price — a piece you offer at $2,000 is worth $2,000 by '
            'your own account — but it can be less. Never shown publicly.')

        self._require_explicit_pricing()

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
        framed_dims_row = Row(
            Column(Field('framed_width_inches'), css_class='col-auto'),
            Column(HTML('<span class="dim-sep">×</span>'), css_class='col-auto align-self-end mb-3'),
            Column(Field('framed_height_inches'), css_class='col-auto'),
            Column(HTML('<span class="dim-sep">×</span>'), css_class='col-auto align-self-end mb-3'),
            Column(Field('framed_depth_inches'), css_class='col-auto'),
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
                'is_sold',
            ),
            Fieldset(
                'Additional details (optional)',
                'start_year',
                HTML('<p class="text-muted small mb-1">Framed size — set these only if '
                     'the piece is framed and its outer dimensions differ from the '
                     'artwork dimensions above. The layout editor and 3D view use '
                     'these when present; leave blank otherwise.</p>'),
                framed_dims_row,
                'hang_drop_inches',
                'agreed_value',
                'description',
                'url',
                'installation',
            ),
        )

    def _require_explicit_pricing(self):
        """Force a deliberate pricing choice on new work.

        The model defaults to Price on Request, so the form arrived with that already
        selected and an artist could submit without ever deciding — which reads as an
        answer but is really an unanswered question, and it is the gallery that finds
        out at hanging time. Existing pieces keep whatever they were saved with.
        """
        from gallery.models import Artwork
        field = self.fields['pricing_type']
        field.required = True
        if self.instance is None or self.instance.pk is None:
            field.choices = ([('', 'Choose how this piece is priced…')]
                             + list(Artwork.PRICING_TYPE_CHOICES))
            field.initial = None
            self.initial.pop('pricing_type', None)

    def clean(self):
        cleaned = super().clean()
        pricing_type = cleaned.get('pricing_type')
        price = cleaned.get('price')
        from gallery.models import Artwork
        if pricing_type == Artwork.PRICING_FOR_SALE and price is None:
            self.add_error('price', 'A price is required when "For Sale" is selected.')
        if pricing_type in (Artwork.PRICING_NFS, Artwork.PRICING_ON_REQUEST):
            cleaned['price'] = None

        # Checked after the line above, so it uses the price that will actually be stored:
        # switching a piece to Not For Sale clears the price, and there is then nothing to
        # measure the agreed value against.
        from gallery.consignment import too_high
        if too_high(cleaned.get('price'), cleaned.get('agreed_value')):
            self.add_error(
                'agreed_value',
                f'This cannot be more than the asking price of '
                f'${cleaned["price"]:,.0f}. A piece you offer at that price is worth that '
                f'much by your own account — lower this, or raise the price.')
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


class OpeningHoursForm(forms.ModelForm):
    class Meta:
        from gallery.models import OpeningHours
        model = OpeningHours
        fields = ('weekday', 'start', 'end', 'by_appointment')
        widgets = {
            'start': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'end': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start'), cleaned.get('end')
        # Rejected rather than silently swapped: "6pm to 11am" is far more likely to be a typo
        # for 6pm–11pm than a genuine overnight opening, and a gallery does not run past
        # midnight. Caught here, or the slot generator would produce an empty or absurd day.
        if start and end and end <= start:
            raise forms.ValidationError('The closing time has to be after the opening time.')
        return cleaned


class SiteClosureForm(forms.ModelForm):
    class Meta:
        from gallery.models import SiteClosure
        model = SiteClosure
        fields = ('start_date', 'end_date', 'note', 'appointments_only')
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'end_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_date'), cleaned.get('end_date')
        if start and end and end < start:
            raise forms.ValidationError('The last day closed cannot be before the first.')
        return cleaned


def _make_hours_formset(**kwargs):
    from django.forms import inlineformset_factory
    from gallery.models import OpeningHours, Site
    return inlineformset_factory(
        Site, OpeningHours, form=OpeningHoursForm,
        extra=kwargs.pop('extra', 3), can_delete=True,
    )


def _make_closure_formset(**kwargs):
    from django.forms import inlineformset_factory
    from gallery.models import Site, SiteClosure
    return inlineformset_factory(
        Site, SiteClosure, form=SiteClosureForm,
        extra=kwargs.pop('extra', 1), can_delete=True,
    )


def _make_obstacle_formset(**kwargs):
    from django.forms import inlineformset_factory
    from gallery.models.room import RoomConfig, WallObstacle
    return inlineformset_factory(
        RoomConfig, WallObstacle, form=WallObstacleForm,
        extra=kwargs.pop('extra', 2), can_delete=True,
    )


class SiteSupportForm(forms.ModelForm):
    """A reusable pedestal/shelf definition (catalog) for a site."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # On a blank "add new" row, don't show the model's default sizes (16/40/16)
        # as if they were real values — leave the row visibly empty so it can't be
        # mistaken for an existing entry.
        if not self.instance.pk:
            for f in ('w_in', 'h_in', 'd_in'):
                self.initial[f] = None

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
            'label': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': '＋ New support — type a name'}),
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
        extra=kwargs.pop('extra', 1), can_delete=True,
    )


class SiteForm(UserAwareModelForm):
    # The zone is filled in from the state on save when that settles it, so this select is
    # for the venues it cannot: the split states, and anywhere outside the US. Built here
    # rather than as model `choices` because 600 names in the field definition get copied
    # into every migration that touches it.
    # Every one of these has a model default, so an existing caller that posts the site form
    # without them must still work. Required-by-default would make five new settings a
    # prerequisite for saving a site at all — which is what it did on the first attempt, and
    # what broke four unrelated tests.
    VISIT_DEFAULTS = ('visit_slot_minutes', 'visit_capacity', 'visit_lead_hours',
                      'visit_horizon_days')

    def _timezone_choices(self):
        from zoneinfo import available_timezones
        return [('', '— derive from the state —')] + [
            (name, name) for name in sorted(available_timezones())]

    class Meta:
        model = Site
        fields = (
            'name',
            # Removed for anyone who is not an admin — see __init__. A director editing
            # their own venue must not be able to appoint more directors, including
            # themselves onto somebody else's venue.
            'directors',
            'street',
            'city',
            'state',
            'postal_code',
            'country',
            'submission_area_label',
            'submission_zipcodes',
            'email',
            'phone',
            # What this venue takes on a sale, and what its consignment agreements say.
            'commission_rate',
            'custody_grace_days',
            'instagram',
            'website',
            'description',
            'hours',
            'about',
            'visit_notes',
            'arrival_note',
            'visit_image',
            # Booking a visit. Listed explicitly because SiteForm names its fields, so a model
            # field added without touching this is unreachable through the UI — which is exactly
            # what happened to these on the first attempt.
            'visits_enabled',
            'visit_slot_minutes',
            'visit_capacity',
            'visit_lead_hours',
            'visit_horizon_days',
            'timezone',
            'image',
            'icon',
            'status',
            'latitude',
            'longitude',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    def clean_custody_grace_days(self):
        value = self.cleaned_data.get('custody_grace_days')
        if value in (None, ''):
            return Site._meta.get_field('custody_grace_days').default
        return value

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Optional on the form, never empty on the model. The column cannot be null — every
        # agreement has to state a cutoff — so clearing the box falls back to the default
        # rather than refusing the whole form over a field most people will never touch.
        self.fields['custody_grace_days'].required = False

        # The venue's state decides its time zone, which is derived from the two-letter
        # code, so a free-text "calif" would silently leave the venue with no zone.
        self.fields['state'].widget = us_states.USStateInput()

        from gallery.permissions import is_staff_user
        if not (self.user and is_staff_user(self.user)):
            self.fields.pop('directors', None)
        else:
            self.fields['directors'].help_text = Site._meta.get_field('directors').help_text
            self.fields['directors'].required = False

        for name in self.VISIT_DEFAULTS:
            self.fields[name].required = False
        self.fields['timezone'] = forms.ChoiceField(
            choices=self._timezone_choices(), required=False,
            label=self.fields['timezone'].label,
            help_text=self.fields['timezone'].help_text)


    def clean(self):
        cleaned = super().clean()
        # Left blank means "leave it as it was", not "zero". A blank slot length would offer no
        # slots at all and look like the feature was broken.
        for name in self.VISIT_DEFAULTS:
            if cleaned.get(name) in (None, ''):
                current = getattr(self.instance, name, None)
                cleaned[name] = current if current not in (None, '') \
                    else Site._meta.get_field(name).default
        # Here rather than in a clean_state: whether the state has to be a US one depends on
        # the country field, which a per-field clean cannot see. The venue's time zone is
        # derived from the two-letter code, so "calif" would leave it with no zone at all.
        try:
            cleaned['state'] = us_states.clean_state(cleaned.get('state'),
                                                     cleaned.get('country'))
        except ValidationError as exc:
            self.add_error('state', exc)
        return cleaned

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
            'submission_scope',
            'max_submissions_per_artist',
            # Blank means "use the venue's rate", which is the normal case.
            'commission_rate',
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
        # A director may only put a show at a venue they run — and only take it off one.
        # Narrowing the queryset is the enforcement, not just the presentation: a
        # ModelMultipleChoiceField rejects any pk outside it, so a posted site id they
        # were never offered fails validation rather than quietly applying.
        from gallery.permissions import directed_site_ids, is_site_director, is_staff_user
        sites = Site.objects.all().order_by('name')
        if user is not None and is_site_director(user) and not is_staff_user(user):
            sites = sites.filter(pk__in=directed_site_ids(user))
        self.fields['sites'].queryset = sites
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

class CampaignForm(forms.ModelForm):
    """Compose a campaign. Both authoring paths on one form, since which one a campaign
    uses is a choice made while writing it, not a different kind of object."""

    class Meta:
        from gallery.models import Campaign
        model = Campaign
        fields = ('site', 'segment', 'subject', 'preheader', 'template_name', 'show',
                  'body_markdown')
        widgets = {
            'subject': forms.TextInput(
                attrs={'placeholder': 'What the inbox shows first'}),
            'preheader': forms.TextInput(
                attrs={'placeholder': 'The line after the subject — around 90 characters'}),
            'body_markdown': forms.Textarea(attrs={'rows': 16}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Offered only when it can actually be sent to, so nobody writes a campaign they will
        # then be told they cannot send. The guard on Campaign.can_send is the real one; this
        # just keeps the form from inviting the mistake.
        from django.conf import settings

        # Default to the deployment's own venue rather than to the top of an alphabetical list
        # or to the network-wide option. Whose list a mailing goes to is the one field where the
        # wrong answer cannot be taken back, so the safe, usual answer is preselected.
        from gallery.models import Subscriber
        if not self.instance.pk and not self.initial.get('site'):
            default = Subscriber.default_site()
            if default is not None:
                self.initial['site'] = default.pk

        if getattr(settings, 'CAMPAIGN_NETWORK_LIST_ENABLED', False):
            self.fields['site'].empty_label = 'Everyone (network-wide list)'
            self.fields['site'].help_text = self.instance._meta.get_field('site').help_text
        else:
            self.fields['site'].empty_label = None
            self.fields['site'].required = True
            self.fields['site'].help_text = (
                'Whose subscribers this goes to. The network-wide (reset.art) list is '
                'unavailable until reset.art has its own email authentication.')

        # Everyone first and preselected. Narrowing a mailing is occasionally right and
        # usually not, and the failure it invites — an opening announcement that quietly went
        # to a fifth of the list — is invisible until somebody asks why they never heard.
        from gallery.models import Subscriber
        self.fields['segment'] = forms.ChoiceField(
            choices=[('', 'Everyone on the list')]
                    + [(value, f'{label}s only') for value, label in Subscriber.SEGMENT_CHOICES],
            required=False, label='Send to',
            help_text='Artists include everyone with an artist profile, whether or not they '
                      'ticked the box themselves.')

        # A dropdown of the MJML templates that actually exist, rather than a text box
        # where a typo becomes a TemplateDoesNotExist at send time.
        # Newest first, and only shows that are actually public or on the way there — mailing
        # about something under consideration would announce a show that may never happen.
        self.fields['show'].queryset = (
            Show.objects.filter(
                status__in=[Show.STATUS_DRAFT, Show.STATUS_PUBLISHED, Show.STATUS_CLOSED,
                            Show.STATUS_OPEN_CALL])
            .prefetch_related('sites').order_by('-start'))
        self.fields['show'].empty_label = 'None — not about a particular show'
        # Dated and located, because a name on its own is not enough to pick from. A gallery
        # accumulates shows with similar names, and the list is not short.
        self.fields['show'].label_from_instance = _show_choice_label
        self.fields['subject'].help_text = (
            'Can use the show\'s details: {{ show.name }}, {{ show.start|date:"j F" }}, '
            '{{ opening.date|date:"l j F" }}. Choosing a template fills in a sensible one.')

        from gallery.campaigns import template_label
        choices = [('', 'None — write the body in Markdown below')]
        choices += [(name, template_label(name)) for name in _campaign_template_names()]
        self.fields['template_name'] = forms.ChoiceField(
            choices=choices, required=False, label='Template',
            help_text='A recurring shape that fills itself from the database. It supplies the '
                      'layout; whether your Markdown body appears inside it is up to the '
                      'template.')

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('template_name') and not (cleaned.get('body_markdown') or '').strip():
            raise forms.ValidationError(
                'Give it a body: either choose a template or write some Markdown.')

        # A subject is a template too, so a mistake in it is a mistake in the one line every
        # recipient reads. Caught here rather than at send time, where the engine deliberately
        # falls back to the raw text rather than failing a whole send over a stray brace.
        subject = cleaned.get('subject') or ''
        if '{%' in subject:
            self.add_error('subject', 'A subject can use {{ show.name }} and the like, but not '
                                      '{% tags %}.')
        elif '{{' in subject:
            from django.template import Template, TemplateSyntaxError
            try:
                Template(subject)
            except TemplateSyntaxError as exc:
                self.add_error('subject', f'That subject will not render: {exc}')

        # A show template with no show renders every field blank. Caught here, where it is a
        # form error next to the field, rather than discovered in a preview that looks broken
        # for no stated reason.
        from gallery.campaigns import template_needs
        template = cleaned.get('template_name')
        show = cleaned.get('show')
        if template and 'show' in template_needs(template) and not show:
            self.add_error('show', 'This template takes its content from a show, so it needs '
                                   'one chosen.')

        # Mailing one venue's subscribers about another venue's show is a mistake nobody makes
        # on purpose and nothing else would catch — the show list has to span sites so any
        # venue's campaign can find its own shows.
        site = cleaned.get('site')
        if show and site and not show.sites.filter(pk=site.pk).exists():
            self.add_error('show', f'“{show.name}” is not at {site.name}. Pick a show at this '
                                   f'venue, or change the list this goes to.')
        return cleaned


def _show_choice_label(show):
    """A show in a dropdown: name, when, and where.

    The name alone is not enough to choose from once a gallery has a few years of them, and the
    consequence of picking the wrong one is a mailing about the wrong show.
    """
    where = ', '.join(site.name for site in show.sites.all())
    when = show.start.strftime('%b %Y') if show.start else 'no date'
    return f'{show.name} — {when}{f" · {where}" if where else ""}'


def _campaign_template_names():
    """MJML campaign templates on disk, so the form can offer them.

    Ordered by CAMPAIGN_TEMPLATES rather than alphabetically, so the list reads in the order a
    show actually happens — announcement, opening, closing — instead of the order the filenames
    happen to sort in, which put closing before opening. Anything on disk but not in the registry
    follows, alphabetically.
    """
    import os
    from django.conf import settings

    from gallery.campaigns import CAMPAIGN_TEMPLATES

    names = set()
    for directory in [os.path.join(str(settings.BASE_DIR), 'templates')]:
        folder = os.path.join(directory, 'email', 'campaigns')
        if os.path.isdir(folder):
            names.update(f for f in os.listdir(folder) if f.endswith('.mjml'))

    known = [name for name in CAMPAIGN_TEMPLATES if name in names]
    return known + sorted(names - set(known))
