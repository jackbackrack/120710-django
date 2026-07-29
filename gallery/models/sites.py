from django.db import models
from django_countries.fields import CountryField
from django.urls import reverse
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit, Transpose

from gallery.models.slugs import build_unique_slug


class Site(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PUBLISHED, 'Published'),
    ]
    PUBLIC_STATUSES = {STATUS_PUBLISHED}

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    street = models.CharField(max_length=255, blank=True, verbose_name='Street address')
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True, verbose_name='State / Province / Region')
    postal_code = models.CharField(max_length=20, blank=True)
    # ISO 3166-1 alpha-2, matching Artist.country, so a national show can compare
    # the two directly. This was free text holding "USA", which meant the
    # comparison needed a table of spellings to guess at.
    country = CountryField(default='US')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    instagram = models.CharField(max_length=100, blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    description = models.TextField(blank=True)
    # The four public info pages (/about/, /visit/, /contact/, /links/) used to be
    # hard-coded templates naming one gallery. They read from here now, falling back to
    # the deployment's default site — so a second gallery gets its own without a deploy.
    hours = models.CharField(
        max_length=255, blank=True, default='', verbose_name='Opening hours',
        help_text='Shown on the Visit and Contact pages, e.g. '
                  '"Sun 1-4p or by Appt MWF 12-6p".')
    about = models.TextField(
        blank=True, default='',
        help_text='The Info page: mission, story, people. Accepts formatting, headings, '
                  'tables and images.')
    visit_notes = models.TextField(
        blank=True, default='', verbose_name='Getting here',
        help_text='Parking, transit and directions, shown on the Visit page below the '
                  'address.')
    visit_image = models.ImageField(
        upload_to='site_visit', blank=True, null=True, verbose_name='Visit photo',
        help_text='A street view or storefront photo for the Visit page.')
    image = models.ImageField(upload_to='site_images', blank=True, null=True)
    card_sm = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=200)], format='JPEG', options={'quality': 80})
    card_md = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=600)], format='JPEG', options={'quality': 80})
    detail_lg = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=1200)], format='JPEG', options={'quality': 85})
    icon = models.ImageField(upload_to='site_icons', blank=True, null=True, help_text='Small logo or icon for the site (shown in nav and cards).')
    icon_sm = ImageSpecField(source='icon', processors=[Transpose(), ResizeToFit(width=32, height=32)], format='PNG', options={'quality': 90})
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    # Which postal codes count as "in this venue's area", for shows whose scope is local.
    # Stored as a list rather than as a rule (a radius, a set of counties) so the boundary
    # stays editable: if one postal code is wrong you fix that postal code, without a
    # deploy and without anyone needing to understand the rule that generated it.
    # `manage.py set_site_catchment` writes both fields; nobody maintains them by hand.
    # Empty means no checking at all, which is what every site does until opted in.
    submission_zipcodes = models.TextField(
        blank=True, default='',
        verbose_name='Local postal codes',
        help_text='Postal codes counting as local to this venue, separated by spaces, '
                  'commas or newlines. Leave blank to disable area checking. Generated '
                  'by `manage.py set_site_catchment`.')
    submission_area_label = models.CharField(
        max_length=120, blank=True, default='',
        verbose_name='Local area name',
        help_text='How the area is described to a curator, e.g. "Bay Area (9 counties)".')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    @property
    def formatted_address(self):
        city_line = ', '.join(filter(None, [self.city, self.state]))
        if self.postal_code:
            city_line = f'{city_line} {self.postal_code}' if city_line else self.postal_code
        # .name, not the field: a CountryField stringifies to its two-letter code,
        # and an address ending in "US" reads like a bug.
        country = self.country.name if self.country else ''
        lines = [l for l in [self.street, city_line, country] if l]
        return '\n'.join(lines)

    def save(self, *args, **kwargs):
        self.slug = build_unique_slug(self, self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('gallery:site_detail', kwargs={'slug': self.slug})
