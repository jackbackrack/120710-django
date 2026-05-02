from accounts.roles import ARTIST_GROUP, CURATOR_GROUP, JUROR_GROUP, STAFF_GROUP


GENERAL_GUIDE = {
    'title': 'Account Basics',
    'summary': 'Use these steps to access your account and keep your profile current.',
    'allowed_actions': [
        'Reset your password from the login page if needed.',
        'Sign in and update your name from the Profile page.',
        'Change your password from ChangePassword in the header.',
    ],
    'forms': [
        {
            'name': 'Name Profile Form',
            'where_used': 'Accounts -> Profile',
            'breadcrumb': 'Home > Profile',
            'fields': [
                {
                    'name': 'first_name',
                    'input_type': 'text input',
                    'purpose': 'Your public first name shown throughout the site.',
                },
                {
                    'name': 'last_name',
                    'input_type': 'text input',
                    'purpose': 'Your public last name shown throughout the site.',
                },
            ],
        },
    ],
}


ROLE_DOCUMENTATION = {
    ARTIST_GROUP: {
        'title': 'Artist Guide',
        'summary': 'Artists manage their own profile and artworks.',
        'allowed_actions': [
            'Create and edit your artist profile.',
            'Create and edit your own artworks.',
            'View and manage your open call submissions.',
        ],
        'forms': [
            {
                'name': 'Artist Form',
                'where_used': 'Artist create/edit pages',
                'breadcrumb': 'Home > Artists > New or Artist Detail > Edit',
                'fields': [
                    {'name': 'first_name', 'input_type': 'text input', 'purpose': 'Given name for your artist profile.'},
                    {'name': 'last_name', 'input_type': 'text input', 'purpose': 'Family name for your artist profile.'},
                    {'name': 'email', 'input_type': 'email input', 'purpose': 'Contact email for curator/admin communication.'},
                    {'name': 'phone', 'input_type': 'text input', 'purpose': 'Optional contact phone number.'},
                    {'name': 'website', 'input_type': 'url/text input', 'purpose': 'External portfolio or personal website URL.'},
                    {'name': 'instagram', 'input_type': 'text input', 'purpose': 'Instagram handle or profile path.'},
                    {'name': 'bio', 'input_type': 'multi-line text area', 'purpose': 'Artist biography shown publicly.'},
                    {'name': 'statement', 'input_type': 'multi-line text area', 'purpose': 'Artist statement shown publicly.'},
                    {'name': 'image', 'input_type': 'file upload', 'purpose': 'Profile image shown on artist pages.'},
                ],
            },
            {
                'name': 'Artwork Form (Artist View)',
                'where_used': 'Artwork create/edit pages',
                'breadcrumb': 'Home > Artists > Your Artist Page > Artworks > New or Edit',
                'fields': [
                    {'name': 'name', 'input_type': 'text input', 'purpose': 'Title of the artwork.'},
                    {'name': 'end_year', 'input_type': 'numeric/text input', 'purpose': 'Ending year for multi-year works.'},
                    {'name': 'start_year', 'input_type': 'numeric/text input', 'purpose': 'Starting year for multi-year works.'},
                    {'name': 'medium', 'input_type': 'text input', 'purpose': 'Primary materials or medium.'},
                    {'name': 'dimensions', 'input_type': 'text input', 'purpose': 'Physical dimensions in your preferred format.'},
                    {'name': 'image', 'input_type': 'file upload', 'purpose': 'Primary artwork image.'},
                    {'name': 'price', 'input_type': 'numeric input', 'purpose': 'Numeric price value when applicable.'},
                    {'name': 'pricing', 'input_type': 'text input', 'purpose': 'Price context like NFS, POA, or edition info.'},
                    {'name': 'replacement_cost', 'input_type': 'numeric input', 'purpose': 'Insurance/replacement value.'},
                    {'name': 'is_sold', 'input_type': 'checkbox', 'purpose': 'Marks the work as sold.'},
                    {'name': 'open_call_available', 'input_type': 'checkbox', 'purpose': 'Allows this artwork to be used in open call workflows.'},
                    {'name': 'description', 'input_type': 'multi-line text area', 'purpose': 'Public description of the work.'},
                    {'name': 'installation', 'input_type': 'multi-line text area', 'purpose': 'Display or install notes.'},
                ],
            },
        ],
    },
    JUROR_GROUP: {
        'title': 'Juror Guide',
        'summary': 'Jurors review artworks only for shows they are assigned to.',
        'allowed_actions': [
            'Access review dashboard for assigned shows only.',
            'Create or update your own review per artwork in a show.',
            'Rate artworks from 1 to 5 and optionally add review notes.',
        ],
        'forms': [
            {
                'name': 'Artwork Review Form',
                'where_used': 'Show review workflow for assigned jurors',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Select Artwork',
                'fields': [
                    {'name': 'rating', 'input_type': 'radio select (1-5)', 'purpose': 'Required score from 1 (lowest) to 5 (highest).'},
                    {'name': 'body', 'input_type': 'multi-line text area', 'purpose': 'Optional qualitative review notes.'},
                ],
            },
        ],
    },
    CURATOR_GROUP: {
        'title': 'Curator Guide',
        'summary': 'Curators manage shows/events, assign jurors, and review aggregated ratings.',
        'allowed_actions': [
            'Manage shows and events you are responsible for.',
            'See all artworks including non-public entries.',
            'Assign jurors to shows and remove assignments.',
            'View all juror reviews plus average ratings and review counts.',
            'Edit juror reviews when curation workflows require it.',
        ],
        'forms': [
            {
                'name': 'Show Form',
                'where_used': 'Show create/edit pages',
                'breadcrumb': 'Home > Shows > New or Show Detail > Edit',
                'fields': [
                    {'name': 'name', 'input_type': 'text input', 'purpose': 'Show title shown in listings and detail pages.'},
                    {'name': 'description', 'input_type': 'multi-line text area', 'purpose': 'Public show description.'},
                    {'name': 'image', 'input_type': 'file upload', 'purpose': 'Hero image for the show.'},
                    {'name': 'is_open_call', 'input_type': 'checkbox', 'purpose': 'Marks the show as open call enabled.'},
                    {'name': 'submission_deadline', 'input_type': 'date/time input', 'purpose': 'Cutoff for open call submissions.'},
                    {'name': 'start', 'input_type': 'date input', 'purpose': 'Show opening/start date.'},
                    {'name': 'end', 'input_type': 'date input', 'purpose': 'Show ending date.'},
                    {'name': 'tags', 'input_type': 'multi-select', 'purpose': 'Categorization tags for filtering and organization.'},
                    {'name': 'artists', 'input_type': 'multi-select', 'purpose': 'Featured artists in the show.'},
                    {'name': 'artworks', 'input_type': 'multi-select', 'purpose': 'Artworks included in the show.'},
                ],
            },
            {
                'name': 'Event Form',
                'where_used': 'Event create/edit pages',
                'breadcrumb': 'Home > Events > New or Event Detail > Edit',
                'fields': [
                    {'name': 'name', 'input_type': 'text input', 'purpose': 'Event title.'},
                    {'name': 'description', 'input_type': 'multi-line text area', 'purpose': 'Public event description.'},
                    {'name': 'show', 'input_type': 'dropdown select', 'purpose': 'Show this event belongs to.'},
                    {'name': 'image', 'input_type': 'file upload', 'purpose': 'Event image shown on detail pages.'},
                    {'name': 'date', 'input_type': 'date input', 'purpose': 'Calendar date for the event.'},
                    {'name': 'start', 'input_type': 'time input', 'purpose': 'Event start time.'},
                    {'name': 'end', 'input_type': 'time input', 'purpose': 'Event end time.'},
                    {'name': 'tags', 'input_type': 'multi-select', 'purpose': 'Event tags used for filtering and curation.'},
                ],
            },
            {
                'name': 'Juror Assignment Form',
                'where_used': 'Show reviews -> Manage jurors',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Manage jurors',
                'fields': [
                    {'name': 'user', 'input_type': 'dropdown select', 'purpose': 'Choose an active user to assign as juror for the show.'},
                ],
            },
            {
                'name': 'Curator Review Edit Form',
                'where_used': 'Artwork reviews detail (curator view)',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Select Artwork > Edit Review',
                'fields': [
                    {'name': 'rating', 'input_type': 'radio select (1-5)', 'purpose': 'Adjust juror score when corrections are required.'},
                    {'name': 'body', 'input_type': 'multi-line text area', 'purpose': 'Adjust juror note text when needed.'},
                ],
            },
        ],
    },
    STAFF_GROUP: {
        'title': 'Staff Guide',
        'summary': 'Staff users have full access across artist, curator, and review workflows.',
        'allowed_actions': [
            'All curator capabilities across all shows/events.',
            'Manage artist and artwork visibility controls globally.',
            'Set or change managing curator fields where available.',
            'Manage curator role assignments and curator tag access.',
        ],
        'forms': [
            {
                'name': 'Artwork Form (Staff Controls)',
                'where_used': 'Artwork create/edit pages',
                'breadcrumb': 'Home > Artworks > New or Artwork Detail > Edit',
                'fields': [
                    {'name': 'artists', 'input_type': 'multi-select', 'purpose': 'Associate one or more artists with the work.'},
                    {'name': 'shows', 'input_type': 'multi-select', 'purpose': 'Associate the artwork with one or more shows.'},
                    {'name': 'is_public', 'input_type': 'checkbox', 'purpose': 'Controls whether artwork is publicly visible.'},
                    {'name': 'tags', 'input_type': 'multi-select', 'purpose': 'Internal/public categorization tags.'},
                ],
            },
            {
                'name': 'Show/Event Staff Fields',
                'where_used': 'Show and event create/edit pages',
                'breadcrumb': 'Home > Shows or Events > New or Detail > Edit',
                'fields': [
                    {'name': 'managing_curator', 'input_type': 'dropdown select', 'purpose': 'Assigns accountable curator user for the record.'},
                    {'name': 'tags', 'input_type': 'multi-select', 'purpose': 'Cross-cutting curation and filtering labels.'},
                ],
            },
            {
                'name': 'Artist Role Update Form',
                'where_used': 'Artist role edit page',
                'breadcrumb': 'Home > Artists > Select Artist > Roles',
                'fields': [
                    {'name': 'is_curator', 'input_type': 'checkbox', 'purpose': 'Grant or revoke curator access for this user.'},
                    {'name': 'curator_tags', 'input_type': 'multi-select', 'purpose': 'Scope curator access to selected tags.'},
                ],
            },
        ],
    },
}