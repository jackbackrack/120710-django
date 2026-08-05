"""One canonical list of US states, and one way of reading what somebody typed.

Every address form took the state as free text, and the artist table shows what that
produces: "c", "b", "ca". `timezone_from_address` keys on the two-letter code, so a
lower-case one silently resolves to nothing, and the values are useless for anything that
has to group or match on them.

So the field offers completion and the value is normalised on the way in — but only for the
United States. Everywhere else "state" means a province, a region, a county or nothing at
all, and a list of fifty American states is the wrong question.
"""
from django.core.exceptions import ValidationError
from django.forms.widgets import TextInput
from django.utils.safestring import mark_safe

# 50 states plus DC. DC is not a state and is on the list anyway: people live there and put
# it in the state box, and rejecting a real address to make a point is not a service.
US_STATES = [
    ('AL', 'Alabama'), ('AK', 'Alaska'), ('AZ', 'Arizona'), ('AR', 'Arkansas'),
    ('CA', 'California'), ('CO', 'Colorado'), ('CT', 'Connecticut'), ('DE', 'Delaware'),
    ('DC', 'District of Columbia'), ('FL', 'Florida'), ('GA', 'Georgia'), ('HI', 'Hawaii'),
    ('ID', 'Idaho'), ('IL', 'Illinois'), ('IN', 'Indiana'), ('IA', 'Iowa'),
    ('KS', 'Kansas'), ('KY', 'Kentucky'), ('LA', 'Louisiana'), ('ME', 'Maine'),
    ('MD', 'Maryland'), ('MA', 'Massachusetts'), ('MI', 'Michigan'), ('MN', 'Minnesota'),
    ('MS', 'Mississippi'), ('MO', 'Missouri'), ('MT', 'Montana'), ('NE', 'Nebraska'),
    ('NV', 'Nevada'), ('NH', 'New Hampshire'), ('NJ', 'New Jersey'), ('NM', 'New Mexico'),
    ('NY', 'New York'), ('NC', 'North Carolina'), ('ND', 'North Dakota'), ('OH', 'Ohio'),
    ('OK', 'Oklahoma'), ('OR', 'Oregon'), ('PA', 'Pennsylvania'), ('RI', 'Rhode Island'),
    ('SC', 'South Carolina'), ('SD', 'South Dakota'), ('TN', 'Tennessee'), ('TX', 'Texas'),
    ('UT', 'Utah'), ('VT', 'Vermont'), ('VA', 'Virginia'), ('WA', 'Washington'),
    ('WV', 'West Virginia'), ('WI', 'Wisconsin'), ('WY', 'Wyoming'),
]

_BY_CODE = {code: code for code, _name in US_STATES}
_BY_NAME = {name.lower(): code for code, name in US_STATES}

DATALIST_ID = 'us-states'


def is_us(country):
    """django_countries gives a Country object; a bare string is just as likely."""
    return str(country or '').upper() in ('US', 'USA')


def normalise(value):
    """"california", "Calif ornia", "ca" → "CA". None when it is not a state at all.

    Accepts the name as readily as the code, because a person typing into a box with
    completion may pick either, and refusing "California" would be perverse.
    """
    text = (value or '').strip()
    if not text:
        return ''
    if text.upper() in _BY_CODE:
        return text.upper()
    return _BY_NAME.get(' '.join(text.lower().split()))


def clean_state(state, country):
    """The value to store, raising ValidationError if the US was claimed and it is not one.

    Outside the US the text is kept as typed: "state" means a province, a region or nothing,
    and there is no list to check it against.
    """
    text = (state or '').strip()
    if not is_us(country):
        return text
    if not text:
        return text
    code = normalise(text)
    if code is None:
        raise ValidationError(
            '“%(value)s” is not a US state. Start typing and pick one from the list, or '
            'use its two-letter abbreviation.',
            params={'value': text})
    return code


def datalist_html():
    """The completion list, as markup a crispy layout can carry.

    A datalist rather than a select: outside the US the same box has to accept anything, and
    one input that suggests fifty options while still taking free text is a smaller thing
    than two widgets and the JavaScript to swap between them.
    """
    options = ''.join(f'<option value="{name}"></option>' for _code, name in US_STATES)
    return f'<datalist id="{DATALIST_ID}">{options}</datalist>'


def widget_attrs():
    return {'list': DATALIST_ID, 'autocomplete': 'address-level1',
            'placeholder': 'e.g. California'}


class USStateInput(TextInput):
    """A text box that carries its own completion list.

    Self-contained on purpose. The alternative was injecting the datalist through each
    form's crispy layout, and SiteForm has no layout — which would have meant finding and
    editing whichever template renders it. A widget travels with the field instead, so any
    form that uses it gets the list without touching a template.
    """

    def __init__(self, attrs=None):
        merged = dict(widget_attrs())
        merged.update(attrs or {})
        super().__init__(attrs=merged)

    def render(self, name, value, attrs=None, renderer=None):
        return mark_safe(super().render(name, value, attrs, renderer) + datalist_html())
