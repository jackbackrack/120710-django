from accounts.roles import ARTIST_GROUP, CURATOR_GROUP, JUROR_GROUP, STAFF_GROUP


GENERAL_GUIDE = {
    'title': 'Account Basics',
    'summary': 'Use these steps to access your account and keep your profile current.',
    'allowed_actions': [
        'Reset your password from the login page if needed.',
        'Sign in and update your name from the Profile page.',
        'Change your password from ChangePassword in the header.',
        'Edit and Delete links only appear when your account can manage that record.',
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
        'summary': 'Artists manage their own profile and artworks, and can submit work to open call shows.',
        'allowed_actions': [
            'Create and edit your artist profile.',
            'Create and edit your own artworks.',
            'Submit artworks to open call shows via the show detail page while the deadline is open.',
            'Track your submission statuses from My Submissions in the navigation.',
            'Your artworks and profile become publicly visible once the show you are part of has opened.',
            'If you already have an artist profile in the system and your account created a new one instead of reusing it, email info@120710.art to ask a staff member to merge them.',
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
                    {'name': 'venmo', 'input_type': 'text input', 'purpose': 'Venmo username starting with @.'},
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
                    {'name': 'description', 'input_type': 'multi-line text area', 'purpose': 'Public description of the work.'},
                    {'name': 'installation', 'input_type': 'multi-line text area', 'purpose': 'Display or install notes.'},
                ],
            },
            {
                'name': 'Open Call Submission Form',
                'where_used': 'Show detail page when the show is accepting submissions',
                'breadcrumb': 'Home > Shows > Select Show > Submit Work',
                'fields': [
                    {'name': 'artwork', 'input_type': 'dropdown select', 'purpose': 'Choose which of your artworks to submit. Each artwork can only be submitted once per show.'},
                    {'name': 'statement', 'input_type': 'multi-line text area', 'purpose': 'Optional artist statement specific to this submission.'},
                ],
            },
        ],
    },
    JUROR_GROUP: {
        'title': 'Juror Guide',
        'summary': 'Jurors review artworks only for shows they are assigned to.',
        'allowed_actions': [
            'Access the review dashboard for shows you are assigned to.',
            'Create or update your own review per artwork in an assigned show.',
            'Rate artworks from 1 to 10 and optionally add qualitative notes.',
            'Each artwork gets one review from you per show; you can return to update it.',
        ],
        'forms': [
            {
                'name': 'Artwork Review Form',
                'where_used': 'Show review workflow for assigned jurors',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Select Artwork',
                'fields': [
                    {'name': 'rating', 'input_type': 'radio select (1-10)', 'purpose': 'Required score from 1 (lowest) to 10 (highest).'},
                    {'name': 'body', 'input_type': 'multi-line text area', 'purpose': 'Optional qualitative review notes.'},
                ],
            },
        ],
    },
    CURATOR_GROUP: {
        'title': 'Curator Guide',
        'summary': 'Curators manage shows/events, run open call workflows, assign jurors, and review aggregated ratings.',
        'allowed_actions': [
            'Manage shows and events you are responsible for.',
            'See all artworks including non-public entries.',
            'Run open call shows: set a submission deadline, review submissions, select or reject each one, then promote selected artworks into the show.',
            'Define a weighted rubric for jury scoring: add named criteria with weights from the Manage rubric criteria page. Jurors score each criterion 1–10; the dashboard shows a weighted composite score per artwork.',
            'Assign jurors to shows and remove assignments.',
            'View all juror reviews plus average ratings (or weighted composite scores when a rubric is defined) per artwork.',
            'Edit juror reviews when curation workflows require it.',
            'If staff promote your linked artist account to curator, your artist profile is made public automatically.',
            'Edit and Delete links are shown only when you can manage the current artist, artwork, show, or event.',
            'Artworks and artists become publicly visible once the show they are part of has opened (start date reached).',
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
                    {'name': 'is_open_call', 'input_type': 'checkbox', 'purpose': 'Marks the show as open call enabled. Adds an Open Call badge and enables artist submissions.'},
                    {'name': 'submission_deadline', 'input_type': 'date input', 'purpose': 'Cutoff date for artist submissions. After this date the show moves to jury review phase automatically.'},
                    {'name': 'decision_date', 'input_type': 'date input', 'purpose': 'Target date for announcing selection decisions (informational, does not enforce a lock).'},
                    {'name': 'start', 'input_type': 'date input', 'purpose': 'Show opening/start date. Artworks and artists become publicly visible from this date.'},
                    {'name': 'end', 'input_type': 'date input', 'purpose': 'Show ending date.'},
                    {'name': 'tags', 'input_type': 'multi-select', 'purpose': 'Categorization tags for filtering and organization.'},
                    {'name': 'artists', 'input_type': 'multi-select', 'purpose': 'Featured artists in the show.'},
                    {'name': 'artworks', 'input_type': 'multi-select', 'purpose': 'Artworks included in the show.'},
                ],
            },
            {
                'name': 'Submission Status Form',
                'where_used': 'Open call submissions review page',
                'breadcrumb': 'Home > Shows > Select Show > Submissions',
                'fields': [
                    {'name': 'status_<id>', 'input_type': 'dropdown select per row', 'purpose': 'Set each submission to Submitted, Selected, or Rejected. Changes are saved on submit. Average juror ratings are shown alongside each entry.'},
                ],
            },
            {
                'name': 'Promote Artworks',
                'where_used': 'Open call promotion page',
                'breadcrumb': 'Home > Shows > Select Show > Promote',
                'fields': [
                    {'name': '(confirm button)', 'input_type': 'form submit', 'purpose': 'Adds all Selected artworks and their artists to the show, then sends acceptance/rejection emails to submitting artists.'},
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
                    {'name': 'artist', 'input_type': 'dropdown select', 'purpose': 'Choose an artist with the juror role to assign as juror for the show.'},
                ],
            },
            {
                'name': 'Rubric Criteria Form',
                'where_used': 'Show reviews -> Manage rubric criteria',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Manage rubric criteria',
                'fields': [
                    {'name': 'name', 'input_type': 'text input', 'purpose': 'Criterion label shown to jurors when scoring, e.g. "Originality" or "Technical execution".'},
                    {'name': 'description', 'input_type': 'multi-line text area', 'purpose': 'Optional guidance to jurors explaining what to consider for this criterion.'},
                    {'name': 'weight', 'input_type': 'numeric input', 'purpose': 'Relative importance of this criterion. Higher weight means it contributes more to the composite score. For example, weights of 2, 1, 1 give the first criterion twice the influence.'},
                    {'name': 'order', 'input_type': 'numeric input', 'purpose': 'Display order shown to jurors. Lower numbers appear first. Use 0, 10, 20, etc. to leave room for reordering.'},
                    {'name': 'DELETE', 'input_type': 'checkbox', 'purpose': 'Check to remove this criterion. Deleting a criterion also removes all juror scores for it.'},
                ],
            },
            {
                'name': 'Curator Review Edit Form',
                'where_used': 'Artwork reviews detail (curator view)',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Select Artwork > Edit Review',
                'fields': [
                    {'name': 'rating', 'input_type': 'radio select (1-10)', 'purpose': 'Adjust juror score when corrections are required.'},
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
            'Grant or revoke juror access for users.',
            'Assign and remove jurors for any show.',
            'View and edit juror reviews across shows when needed.',
            'Promoting an artist user to curator also sets that linked artist profile to public visibility.',
            'Edit and Delete links in list/detail pages are permission-gated and shown only to managers of each record.',
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
                    {'name': 'is_curator', 'input_type': 'checkbox', 'purpose': 'Grant or revoke curator access for this user. When granted, linked artist profiles become public.'},
                    {'name': 'is_juror', 'input_type': 'checkbox', 'purpose': 'Grant or revoke juror access for this user.'},
                    {'name': 'curator_tags', 'input_type': 'multi-select', 'purpose': 'Scope curator access to selected tags.'},
                ],
            },
            {
                'name': 'Juror Assignment Form',
                'where_used': 'Show reviews -> Manage jurors',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Manage jurors',
                'fields': [
                    {'name': 'artist', 'input_type': 'dropdown select', 'purpose': 'Assign an artist with the juror role as juror for the selected show.'},
                ],
            },
            {
                'name': 'Juror Review Edit Form (Staff)',
                'where_used': 'Artwork reviews detail (staff/curator view)',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Select Artwork > Edit Review',
                'fields': [
                    {'name': 'rating', 'input_type': 'radio select (1-10)', 'purpose': 'Adjust juror score when corrections are required.'},
                    {'name': 'body', 'input_type': 'multi-line text area', 'purpose': 'Adjust juror note text when needed.'},
                ],
            },
        ],
    },
}