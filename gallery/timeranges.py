"""Formatting a span of clock time, in one place.

Event times and opening hours are the same problem — "when is this open, from when to when" —
and were about to be the same code written twice. They read identically because they are the same
function: `4:00–8:00 PM`, with the meridiem dropped from the start when both ends share it.

Kept out of any model so both `Event` and `OpeningHours` can use it without importing each other.
"""

# The day names a reader expects, in the order Python numbers them: Monday is 0, matching
# `datetime.date.weekday()`. Storing that number rather than a string means "is the gallery open
# on this date" is an integer comparison rather than a lookup table.
WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
WEEKDAY_ABBR = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
# schema.org's two-letter forms, in the same order.
WEEKDAY_SCHEMA = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']


def clock(when, meridiem=True):
    """A single time: `4 PM`, `4:30 PM`, or the same without the meridiem.

    On the hour, the `:00` is dropped. Nobody says "six oh oh", and these sit on lines that are
    already tight — an event heading carries a name, a date and two controls beside them.
    """
    hour = when.hour % 12 or 12
    text = f'{hour}' if when.minute == 0 else f'{hour}:{when.minute:02d}'
    return f'{text} {"AM" if when.hour < 12 else "PM"}' if meridiem else text


def time_range(start, end):
    """`4–8 PM`, or `11 AM–2 PM` when the two ends straddle noon.

    Dropping the repeated meridiem is how anybody writes an opening time by hand. Keeping it
    across noon is not a nicety: `11–2 PM` would read as the wrong half of the day.
    """
    same_half = (start.hour < 12) == (end.hour < 12)
    return f'{clock(start, not same_half)}–{clock(end)}'


def weekday_ranges(numbers):
    """`Mon–Wed, Fri` from [0, 1, 2, 4] — runs collapsed, single days left alone.

    Written out, a week of identical hours is seven lines nobody reads. Collapsed, it is the one
    line a visitor is looking for.
    """
    days = sorted(set(numbers))
    if not days:
        return ''
    runs, run = [], [days[0]]
    for day in days[1:]:
        if day == run[-1] + 1:
            run.append(day)
        else:
            runs.append(run)
            run = [day]
    runs.append(run)

    parts = []
    for run in runs:
        if len(run) == 1:
            parts.append(WEEKDAY_ABBR[run[0]])
        elif len(run) == 2:
            # Two days is not a range anybody says out loud — "Mon, Tue", not "Mon–Tue".
            parts.append(f'{WEEKDAY_ABBR[run[0]]}, {WEEKDAY_ABBR[run[1]]}')
        else:
            parts.append(f'{WEEKDAY_ABBR[run[0]]}–{WEEKDAY_ABBR[run[-1]]}')
    return ', '.join(parts)
