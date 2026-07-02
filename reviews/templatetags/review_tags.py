from django import template

register = template.Library()

_SCORE_LABELS = {10: 'Weak', 30: 'Developing', 50: 'Solid', 70: 'Strong', 90: 'Exceptional'}


@register.filter
def score_label(value):
    if value is None:
        return '—'
    try:
        return _SCORE_LABELS.get(int(value), str(value))
    except (ValueError, TypeError):
        return str(value)
