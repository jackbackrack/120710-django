from django.utils.text import slugify


def build_unique_slug(instance, value, slug_field_name='slug'):
    field = instance._meta.get_field(slug_field_name)
    max_length = field.max_length
    base_slug = slugify(value or '') or instance._meta.model_name
    base_slug = base_slug[:max_length].strip('-') or instance._meta.model_name

    queryset = instance.__class__.objects.all()
    if instance.pk:
        queryset = queryset.exclude(pk=instance.pk)

    candidate = base_slug
    suffix = 2
    while queryset.filter(**{slug_field_name: candidate}).exists():
        suffix_text = f'-{suffix}'
        trimmed_base = base_slug[: max_length - len(suffix_text)].strip('-') or instance._meta.model_name
        candidate = f'{trimmed_base}{suffix_text}'
        suffix += 1

    return candidate