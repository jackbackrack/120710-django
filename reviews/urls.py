from django.urls import re_path

from reviews.views import artwork_review, copy_rubric_from_show, curator_edit_review, manage_rubric_criteria, review_data, save_score, show_juror_assignment, show_review_dashboard

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
        r'^show/(?P<show_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/reviews/rubric/$',
        manage_rubric_criteria,
        name='manage_rubric_criteria',
    ),
    re_path(
        r'^show/(?P<show_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/reviews/rubric/copy/$',
        copy_rubric_from_show,
        name='copy_rubric_from_show',
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
    re_path(
        r'^show/(?P<show_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/review-data/$',
        review_data,
        name='review_data',
    ),
    re_path(
        r'^show/(?P<show_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/save-score/$',
        save_score,
        name='save_score',
    ),
]
