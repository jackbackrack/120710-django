"""A visitor booking a time to come and see the gallery.

Deliberately small, and deliberately not a reservation system. Slots are **shared**: several
people may book the same half hour, because for a gallery that is fewer appointments to keep
rather than a clash. That single decision removes almost everything that makes scheduling hard —
no locking, no capacity reconciliation, no double-booking races, no held-then-abandoned slots.

The gallery learns about a booking by email, as a calendar invitation, which Google Calendar adds
to the owner's calendar by itself. There is no Google integration to configure, no OAuth, no
tokens to refresh, and nothing to go stale. See gallery/visits.py.
"""
from django.db import models


class Visit(models.Model):
    site = models.ForeignKey('gallery.Site', on_delete=models.CASCADE, related_name='visits')
    # The slot start, timezone-aware and stored in UTC like every other datetime here.
    when = models.DateTimeField()
    # Snapshotted rather than read from the site: a booking that was made when slots were half
    # an hour stays half an hour, even after somebody changes the setting.
    minutes = models.PositiveSmallIntegerField(default=30)

    name = models.CharField(max_length=150)
    email = models.EmailField()
    party_size = models.PositiveSmallIntegerField(default=1)
    note = models.TextField(blank=True, default='')

    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Bumped whenever an invitation is re-sent for this booking. Calendar clients ignore an
    # update whose SEQUENCE has not advanced, so a cancellation sent at the same sequence as the
    # invitation is silently dropped and the appointment stays in the calendar forever.
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['when']
        indexes = [models.Index(fields=['site', 'when'])]

    def __str__(self):
        return f'{self.name} at {self.site.name} on {self.when:%Y-%m-%d %H:%M}'

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None

    @property
    def ends(self):
        import datetime
        return self.when + datetime.timedelta(minutes=self.minutes)

    def uid(self, domain='120710.art'):
        """Stable across the invitation and its cancellation, which is what makes them a pair."""
        return f'visit-{self.pk}@{domain}'
