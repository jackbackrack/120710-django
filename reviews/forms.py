from django import forms
from django.contrib.auth.models import User

from reviews.models import ArtworkReview


class ArtworkReviewForm(forms.ModelForm):
    rating = forms.TypedChoiceField(
        choices=[(i, str(i)) for i in range(1, 6)],
        coerce=int,
        widget=forms.RadioSelect,
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


class ShowJurorAssignmentForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by('username'),
        required=True,
        label='User',
    )
