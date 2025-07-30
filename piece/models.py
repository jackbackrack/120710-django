from django.contrib.auth.models import User
from django.db import models

class Artist(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255)
    phone = models.CharField(max_length=255)
    website = models.URLField(max_length=255, blank=True, null=True)
    instagram = models.CharField(max_length=255, blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    statement = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='artist_images', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Show(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='show_images', blank=True, null=True)
    curator = models.ForeignKey(Artist, related_name='shows', on_delete=models.CASCADE)
    curators = models.ManyToManyField(Artist)
    opening = models.DateField()
    opening_start = models.TimeField()
    opening_end = models.TimeField()
    closing = models.DateField(blank=True, null=True)
    closing_start = models.TimeField()
    closing_end = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Piece(models.Model):
    name = models.CharField(max_length=255)
    shows = models.ManyToManyField(Show)
    artists = models.ManyToManyField(Artist)
    end_year = models.IntegerField()
    start_year = models.IntegerField(blank=True, null=True)
    medium = models.TextField(blank=True, null=True)
    dimensions = models.CharField(max_length=255, blank=True, null=True)
    image = models.ImageField(upload_to='piece_images', blank=True, null=True)
    price = models.FloatField(blank=True, null=True)
    pricing = models.CharField(max_length=255, blank=True, null=True)
    replacement_cost = models.FloatField(blank=True, null=True)
    is_sold = models.BooleanField(default=False)
    description = models.TextField(blank=True, null=True)
    installation = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

