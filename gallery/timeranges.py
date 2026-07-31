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


def short_date(day, today=None):
    """`Aug 5`, or `Aug 5, 2027` when it is not this year.

    The year is noise nine times in ten — a listing of what is on now does not need to keep
    saying which year now is — and essential the tenth, when something is genuinely next year.
    """
    import datetime as _dt

    if day is None:
        return ''
    today = today or _dt.date.today()
    return day.strftime('%b %-d') if day.year == today.year else day.strftime('%b %-d, %Y')


def short_date_range(start, end, today=None):
    """`Aug 5`, `Aug 5 – 9`, `Aug 5 – Sep 3`, or with years when they are not this one.

    Deliberately separate from Show.date_range, which keeps the year always: that one goes into
    the catalogue and the checklist, and a printed page read in five years needs to say which
    year it is talking about. This is for the screen.
    """
    import datetime as _dt

    if start is None or end is None:
        return ''
    today = today or _dt.date.today()
    this_year = start.year == end.year == today.year

    if start == end:
        return short_date(start, today)
    if start.year != end.year:
        return f'{short_date(start, today)} – {short_date(end, today)}'
    if start.month == end.month:
        tail = f'{end.day}' if this_year else f'{end.day}, {end.year}'
        return f'{start.strftime("%b %-d")} – {tail}'
    tail = end.strftime('%b %-d') if this_year else end.strftime('%b %-d, %Y')
    return f'{start.strftime("%b %-d")} – {tail}'
