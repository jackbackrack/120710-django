from django import template
from django.template.defaultfilters import stringfilter

register = template.Library()

@register.filter
@stringfilter
def normalize_instagram(value):
    """
    Normalizes a string value to have one '@' at the beginning.
    """
    # Remove any existing '@' symbols from the beginning
    while value.startswith('@'):
        value = value[1:]
        
    # Add the single '@' prefix
    return '@' + value
