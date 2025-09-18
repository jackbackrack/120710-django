import datetime
from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse

class Artist(models.Model):
    user = models.ForeignKey(User, related_name='artists', on_delete=models.CASCADE, blank=True, null=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255)
    phone = models.CharField(max_length=255)
    website = models.URLField(max_length=255, blank=True, null=True)
    instagram = models.CharField(max_length=255, blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    statement = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='artist_images', null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("piece:artist_detail", kwargs={"pk": self.pk})

class Show(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='show_images', blank=True, null=True)
    curators = models.ManyToManyField(Artist)
    start = models.DateField(default=datetime.date.today)
    end = models.DateField(default=datetime.date.today)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("piece:show_detail", kwargs={"pk": self.pk})

class Event(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    show = models.ForeignKey(Show, related_name="events", on_delete=models.CASCADE)
    image = models.ImageField(upload_to='show_images', blank=True, null=True)
    date = models.DateField()
    start = models.TimeField()
    end = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return self.show.name + " " + self.name

    def get_absolute_url(self):
        return reverse("piece:event_detail", kwargs={"pk": self.pk})

class Piece(models.Model):
    name = models.CharField(max_length=255)
    shows = models.ManyToManyField(Show, related_name="pieces")
    artists = models.ManyToManyField(Artist, related_name="pieces")
    end_year = models.IntegerField()
    start_year = models.IntegerField(blank=True, null=True)
    medium = models.TextField(blank=True, null=True)
    dimensions = models.CharField(verbose_name="Dimensions: LxWxD in inches", max_length=255, blank=True, null=True)
    image = models.ImageField(upload_to='piece_images', null=True)
    price = models.FloatField(verbose_name="Price: Numeric price", blank=True, null=True)
    pricing = models.CharField(verbose_name='Pricing: anything more sophisticated like "Upon request" or "NFS"', max_length=255, blank=True, null=True)
    replacement_cost = models.FloatField(verbose_name="Replacment Cost: redo cost in the rare case that it gets stolen or damaged", blank=True, null=True)
    is_sold = models.BooleanField(default=False)
    description = models.TextField(blank=True, null=True)
    installation = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("piece:piece_detail", kwargs={"pk": self.pk})

