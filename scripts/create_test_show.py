#!/usr/bin/env python
"""
Create a test show.

Usage:
    python create_test_show.py --name NAME [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                               [--submission-deadline YYYY-MM-DD]
                               [--review-deadline YYYY-MM-DD]
                               [--decision-date YYYY-MM-DD]
                               [--image IMAGE_PATH]
                               [--curator EMAIL]
                               [--status STATUS]

  --name NAME                   Show name (required).
  --start YYYY-MM-DD            Show start date (default: today).
  --end YYYY-MM-DD              Show end date (default: today).
  --submission-deadline DATE    Submission deadline date.
  --review-deadline DATE        Review deadline date.
  --decision-date DATE          Decision date.
  --image IMAGE_PATH            Path to show image.
  --curator EMAIL               Email of artist to set as curator (repeatable).
  --status STATUS               Initial status (default: under_consideration).
                                Choices: under_consideration, open_call, in_review,
                                         draft, published, closed.

Example:
    python create_test_show.py --name "Spring Show" --start 2026-03-01 --end 2026-03-31 \\
                               --submission-deadline 2026-02-01 \\
                               --curator curator@example.com \\
                               --image ~/Downloads/show.jpg
"""
import os
import sys
import datetime

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eatart.settings')
django.setup()

from django.core.files import File
from gallery.models import Artist, Show


def _pop_flag_value(args, flag):
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            value = args[idx + 1]
            args.remove(flag)
            args.remove(value)
            return value
        args.remove(flag)
    return None

def _pop_flag_values(args, flag):
    values = []
    while flag in args:
        values.append(_pop_flag_value(args, flag))
    return values

def parse_date(s, label):
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        print(f'Invalid date for {label}: {s!r} (expected YYYY-MM-DD)')
        sys.exit(1)


args = sys.argv[1:]

name                = _pop_flag_value(args, '--name')
start_str           = _pop_flag_value(args, '--start')
end_str             = _pop_flag_value(args, '--end')
submission_deadline = _pop_flag_value(args, '--submission-deadline')
review_deadline     = _pop_flag_value(args, '--review-deadline')
decision_date       = _pop_flag_value(args, '--decision-date')
image_path          = _pop_flag_value(args, '--image')
status              = _pop_flag_value(args, '--status') or Show.STATUS_UNDER_CONSIDERATION
curator_emails      = _pop_flag_values(args, '--curator')

if not name:
    print('--name is required')
    print(__doc__)
    sys.exit(1)

today = datetime.date.today()
start = parse_date(start_str, '--start') if start_str else today
end   = parse_date(end_str,   '--end')   if end_str   else today

if submission_deadline:
    submission_deadline = parse_date(submission_deadline, '--submission-deadline')
if review_deadline:
    review_deadline = parse_date(review_deadline, '--review-deadline')
if decision_date:
    decision_date = parse_date(decision_date, '--decision-date')
if image_path:
    image_path = os.path.expanduser(image_path)

valid_statuses = {s for s, _ in Show.STATUS_CHOICES}
if status not in valid_statuses:
    print(f'Invalid --status {status!r}. Choices: {", ".join(valid_statuses)}')
    sys.exit(1)

show = Show(
    name=name,
    start=start,
    end=end,
    submission_deadline=submission_deadline,
    review_deadline=review_deadline,
    decision_date=decision_date,
    status=status,
)

if image_path:
    if not os.path.exists(image_path):
        print(f'Image file not found: {image_path}')
        sys.exit(1)
    with open(image_path, 'rb') as f:
        show.image.save(os.path.basename(image_path), File(f), save=False)

show.save()

for email in curator_emails:
    artist = Artist.objects.filter(email__iexact=email).first()
    if not artist:
        artist = Artist.objects.filter(user__email__iexact=email).first()
    if not artist:
        print(f'Warning: no artist found for curator email: {email}')
    else:
        show.curators.add(artist)

print(f'Created show "{show.name}" (pk={show.pk}, status={show.status}).')
