from django.urls import re_path

from reviews.views import artwork_review, curator_edit_review, show_juror_assignment, show_review_dashboard

app_name = 'reviews'

urlpatterns = [
    re_path(
        r'^show/(?P<show_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/reviews/$',
        show_review_dashboard,
        name='show_review_dashboard',
    ),
    re_path(
        r'^show/(?P<show_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/reviews/jurors/$',
        show_juror_assignment,
        name='show_juror_assignment',
    ),
    re_path(
        r'^show/(?P<show_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/reviews/(?P<artwork_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/$',
        artwork_review,
        name='artwork_review',
    ),
    re_path(
        r'^show/(?P<show_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/reviews/(?P<artwork_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/(?P<review_id>[0-9]+)/edit/$',
        curator_edit_review,
        name='curator_edit_review',
    ),
]
