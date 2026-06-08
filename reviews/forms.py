from django import forms
from django.forms import modelformset_factory

from gallery.models import Artist
from reviews.models import ArtworkReview, CriterionScore, RubricCriterion


class ArtworkReviewForm(forms.ModelForm):
    rating = forms.TypedChoiceField(
        choices=[(i, str(i)) for i in range(1, 11)],
        coerce=int,
        widget=forms.RadioSelect,
        required=False,
        empty_value=None,
        label='Overall rating',
    )

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
            weight_label = f'{criterion.weight:g}' if criterion.weight != int(criterion.weight) else str(int(criterion.weight))
            label = f'{criterion.name} — weight {weight_label}'
            if criterion.description:
                label += f' ({criterion.description})'
            self.fields[field_key] = forms.TypedChoiceField(
                choices=[(i, str(i)) for i in range(1, 11)],
                coerce=int,
                widget=forms.RadioSelect,
                label=label,
                required=True,
            )
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
    fields=['name', 'description', 'weight', 'order'],
    extra=1,
    can_delete=True,
    widgets={
        'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional description shown to jurors'}),
        'weight': forms.NumberInput(attrs={'step': '0.1', 'min': '0.1', 'style': 'width:5em'}),
        'order': forms.NumberInput(attrs={'min': '0', 'style': 'width:4em'}),
    },
)


class ShowJurorAssignmentForm(forms.Form):
    artist = forms.ModelChoiceField(
        queryset=Artist.objects.filter(user__isnull=False, user__is_active=True).order_by('name'),
        required=True,
        label='Artist',
    )
