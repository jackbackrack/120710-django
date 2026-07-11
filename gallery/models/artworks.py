import os
import re

from django.db import models
from django.urls import reverse
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFit, Transpose

from gallery.models.exhibitions import Show
from gallery.models.people import Artist, _sanitize_upload_filename
from gallery.models.slugs import build_unique_slug


def artwork_image_upload(instance, filename):
    return _sanitize_upload_filename('artwork_images', filename)


class ArtworkImage(models.Model):
    artwork = models.ForeignKey('Artwork', related_name='supplemental_images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to=artwork_image_upload)
    order = models.IntegerField(default=0)
    card_sm = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=200)], format='JPEG', options={'quality': 80})
    card_md = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=600)], format='JPEG', options={'quality': 80})
    slideshow = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=1920)], format='JPEG', options={'quality': 85})

    class Meta:
        ordering = ['order', 'pk']


class Artwork(models.Model):
    name = models.CharField(max_length=255, help_text='Title of artwork')
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    shows = models.ManyToManyField(Show, related_name='artworks', blank=True)
    artists = models.ManyToManyField(Artist, related_name='artworks')
    created_by = models.ForeignKey(
        'auth.User',
        related_name='created_artworks',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    end_year = models.IntegerField()
    start_year = models.IntegerField(verbose_name='Start_year: only fill in if different than end_year', blank=True, null=True)
    medium = models.TextField(blank=True, null=True)
    dimensions = models.CharField(verbose_name='Dimensions: LxWxD in inches', max_length=255, blank=True, null=True)
    width_inches = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True, verbose_name='Width (inches)')
    height_inches = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True, verbose_name='Height (inches)')
    depth_inches = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True, verbose_name='Depth (inches, optional)')
    image = models.ImageField(upload_to=artwork_image_upload, blank=True, null=True)
    card_sm = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=200)], format='JPEG', options={'quality': 80})
    card_md = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=600)], format='JPEG', options={'quality': 80})
    detail_lg = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=1200)], format='JPEG', options={'quality': 85})
    slideshow = ImageSpecField(source='image', processors=[Transpose(), ResizeToFit(width=1920)], format='JPEG', options={'quality': 85})
    PRICING_FOR_SALE = 'for_sale'
    PRICING_ON_REQUEST = 'on_request'
    PRICING_BEST_OFFER = 'best_offer'
    PRICING_NFS = 'nfs'
    PRICING_TYPE_CHOICES = [
        (PRICING_FOR_SALE, 'For Sale'),
        (PRICING_ON_REQUEST, 'Price on Request'),
        (PRICING_BEST_OFFER, 'Best Offer'),
        (PRICING_NFS, 'Not For Sale'),
    ]
    pricing_type = models.CharField(
        max_length=20,
        choices=PRICING_TYPE_CHOICES,
        default=PRICING_ON_REQUEST,
        verbose_name='Pricing',
    )
    price = models.FloatField(verbose_name='Price ($)', blank=True, null=True)
    replacement_cost = models.FloatField(verbose_name='Replacment Cost: redo cost in the rare case that it gets stolen or damaged', blank=True, null=True)
    is_sold = models.BooleanField(default=False)
    description = models.TextField(blank=True, null=True)
    url = models.URLField(blank=True, null=True, verbose_name='URL (video, website, or other supporting link)')
    installation = models.TextField(verbose_name='Installation: optional instructions for installing your work', blank=True, null=True)
    tags = models.ManyToManyField('gallery.Tag', related_name='artworks', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def placard_dimensions(self):
        """W × H (× D) in — formatted dimension string."""
        def fmt(v):
            f = float(v)
            return str(int(f)) if f == int(f) else str(round(f, 2))
        dims = [fmt(v) for v in (self.width_inches, self.height_inches, self.depth_inches) if v]
        return (' × '.join(dims) + ' in') if len(dims) >= 2 else ''

    @property
    def placard_meta(self):
        """Year • Medium • dimensions — single-line for contexts that need it."""
        parts = [p for p in (
            str(self.end_year) if self.end_year else '',
            self.medium.strip() if self.medium else '',
            self.placard_dimensions,
        ) if p]
        return ' • '.join(parts)

    @property
    def formatted_price(self):
        if self.pricing_type == self.PRICING_NFS:
            return 'Not For Sale'
        if self.pricing_type == self.PRICING_ON_REQUEST:
            return 'Price on Request'
        if self.pricing_type == self.PRICING_BEST_OFFER:
            if self.price is not None:
                amount = int(self.price) if self.price == int(self.price) else self.price
                return f'Best Offer (min ${amount:,})'
            return 'Best Offer'
        if self.pricing_type == self.PRICING_FOR_SALE and self.price is not None:
            amount = int(self.price) if self.price == int(self.price) else self.price
            return f'${amount:,}'
        return ''

    @property
    def formatted_dimensions(self):
        if self.width_inches is not None and self.height_inches is not None:
            fmt = lambda v: f'{v:g}"'
            parts = [fmt(self.width_inches), fmt(self.height_inches)]
            if self.depth_inches is not None:
                parts.append(fmt(self.depth_inches))
            return ' × '.join(parts)
        return self.dimensions or ''

    def save(self, *args, **kwargs):
        self.name = (self.name or '').strip()
        self.slug = build_unique_slug(self, self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('gallery:artwork_detail', kwargs={'slug': self.slug})
