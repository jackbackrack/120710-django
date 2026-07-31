"""Somebody saying whether they are coming to an event.

Structurally a `Visit` without the slot-picking: a name, a way to reach them, and how many. The
same choices apply — no account, a signed link to change their mind, shared rather than ticketed
— because the thing being avoided is the same. An RSVP that feels like buying a ticket turns a
warm invitation into an administrative transaction.

**Yes, maybe or no**, rather than a single "I'm coming" button. Each earns its place:

  * *Maybe* is the honest answer weeks out, and offering it is what stops somebody closing the
    page having said nothing. A maybe is also the person a reminder helps most — they have not
    decided, and the night before is when they will.
  * *No* looks useless and is not: it takes them off the reminder, which is the difference
    between a service and a nuisance, and it tells the gallery that interest existed even where
    attendance did not.

There is no separate cancellation. Changing your answer to *no* is the cancellation, which is one
mechanism rather than two and reads better to the person doing it.

The count is not the point. What an RSVP actually buys is **permission to send a reminder**: to
the whole mailing list that is a second campaign and slightly spammy, and to somebody who said
they were coming it is a service they asked for. Forgetting is what goes wrong between the
announcement and the night, far more often than deciding not to come.
"""
from django.db import models


class EventRsvp(models.Model):
    YES = 'yes'
    MAYBE = 'maybe'
    NO = 'no'
    RESPONSE_CHOICES = [
        (YES, 'Coming'),
        (MAYBE, 'Maybe'),
        (NO, "Can't make it"),
    ]
    # The ones worth reminding: a yes may have forgotten, and a maybe has not decided.
    REMINDABLE = (YES, MAYBE)

    event = models.ForeignKey('gallery.Event', on_delete=models.CASCADE, related_name='rsvps')
    name = models.CharField(max_length=150)
    email = models.EmailField()
    response = models.CharField(max_length=8, choices=RESPONSE_CHOICES, default=YES)
    # "+ how many", not "will you attend": bringing somebody becomes the default rather than a
    # decision, and arriving alone is one of the real reasons people do not come.
    party_size = models.PositiveSmallIntegerField(default=1)
    note = models.TextField(blank=True, default='')

    reminded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            # One per address per event. A second reply is somebody changing their mind, not a
            # second guest, and counting both would inflate what the gallery caters for.
            models.UniqueConstraint(fields=['event', 'email'], name='one_rsvp_per_email'),
        ]
        indexes = [models.Index(fields=['event', 'response'])]

    def __str__(self):
        return f'{self.name} → {self.event.name} ({self.response})'

    @property
    def is_coming(self):
        return self.response == self.YES

    @property
    def wants_reminding(self):
        return self.response in self.REMINDABLE
