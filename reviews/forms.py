from django import forms
from django.forms import modelformset_factory

from gallery.models import Artist
from reviews.models import ArtworkReview, CriterionScore, RubricCriterion

SCORE_CHOICES = [
    (10, 'Weak'),
    (30, 'Developing'),
    (50, 'Solid'),
    (70, 'Strong'),
    (90, 'Exceptional'),
]


def _score_field(label, required=True):
    return forms.TypedChoiceField(
        choices=SCORE_CHOICES,
        coerce=int,
        required=required,
        label=label,
        widget=forms.RadioSelect,
    )


class ArtworkReviewForm(forms.ModelForm):
    rating = _score_field('Overall rating', required=False)

    class Meta:
        model = ArtworkReview
        fields = ['rating', 'body']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Your review (optional)'}),
        }
        labels = {
            'body': 'Review notes',
        }

    def __init__(self, *args, show=None, **kwargs):
        instance = kwargs.get('instance')
        super().__init__(*args, **kwargs)
        self.criteria = list(show.rubric_criteria.all()) if show else []

        existing_scores = {}
        if instance and instance.pk:
            for cs in CriterionScore.objects.filter(review=instance).select_related('criterion'):
                existing_scores[cs.criterion_id] = cs.score

        for criterion in self.criteria:
            field_key = f'criterion_{criterion.pk}'
            label = f'{criterion.name} — {criterion.percentage:g}%'
            if criterion.description:
                label += f' ({criterion.description})'
            self.fields[field_key] = _score_field(label)
            if criterion.pk in existing_scores:
                self.initial[field_key] = existing_scores[criterion.pk]

        if self.criteria:
            del self.fields['rating']
            self.fields['body'] = self.fields.pop('body')
        else:
            for key in ['rating', 'body']:
                self.fields[key] = self.fields.pop(key)

    def save_criterion_scores(self, review):
        for criterion in self.criteria:
            score = self.cleaned_data.get(f'criterion_{criterion.pk}')
            if score is not None:
                CriterionScore.objects.update_or_create(
                    review=review,
                    criterion=criterion,
                    defaults={'score': score},
                )


RubricCriterionFormSet = modelformset_factory(
    RubricCriterion,
    fields=['name', 'description', 'percentage', 'order'],
    extra=1,
    can_delete=True,
    widgets={
        'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional description shown to jurors'}),
        'percentage': forms.NumberInput(attrs={'step': '1', 'min': '0', 'max': '100', 'style': 'width:5em'}),
        'order': forms.NumberInput(attrs={'min': '0', 'style': 'width:4em'}),
    },
)


class ShowJurorAssignmentForm(forms.Form):
    artist = forms.ModelChoiceField(
        queryset=Artist.objects.filter(user__isnull=False, user__is_active=True).order_by('name'),
        required=True,
        label='Artist',
    )
