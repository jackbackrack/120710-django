HOW_TO_GUIDES = [
    {
        'title': 'How to sign up for an account',
        'steps': [
            'Go to the Sign Up page via the LogIn link in the header, then choose "Sign up".',
            'Enter your first name, last name, email, and password — or use the "Continue with Google" option to sign in with your Google account.',
            'After signing up, an artist profile is automatically created for you using your name and email.',
            'If you already have an artist profile in the system (e.g. you were added by a staff member before you signed up), and signing up created a duplicate profile, email info@120710.art to have a staff member merge them.',
        ],
    },
    {
        'title': 'How to add your artist information',
        'steps': [
            'Sign in, then go to Artists in the navigation.',
            'Click New at the top of the Artists page to create or fill in your artist profile.',
            'If your profile already exists (created when you signed up), find it in the Artists list and click Edit.',
            'Fill in your bio, statement, website, Instagram handle, Venmo, and upload a profile photo.',
            'Your profile will become publicly visible once you are part of a show whose start date has passed, or once a staff member adds you as a curator of a show.',
        ],
    },
    {
        'title': 'How to add artworks',
        'steps': [
            'Sign in, then go to Artworks in the navigation.',
            'Click New at the top of the Artworks page.',
            'Fill in the title, year, medium, dimensions (width × height × depth), and upload an image — image is required for new artworks.',
            'Optionally add price, pricing notes (e.g. NFS, POA), replacement cost, and a description.',
            'Newly added artworks are private until they are included in a show whose start date has passed.',
        ],
    },
    {
        'title': 'How to submit artwork to an open call show',
        'steps': [
            'You must have at least one artwork already added to your account before you can submit.',
            'Go to Shows in the navigation and find a show with an "Open Call" badge — this means it is currently accepting submissions.',
            'Open the show detail page and click "Submit Artwork".',
            'Select the artwork you want to submit from the dropdown (each artwork can only be submitted once per show).',
            'Optionally add an artist statement specific to this submission.',
            'You can track your submission status on the show detail page and retract it while the deadline is still open.',
            'You will receive an email when the curator makes a selection decision.',
        ],
    },
    {
        'title': 'How to create a show (staff only)',
        'steps': [
            'Sign in as a staff user, then go to Shows in the navigation.',
            'Click New at the top of the Shows page.',
            'Enter the show name, dates, description, and upload a hero image.',
            'Add artists and artworks directly using the multi-select fields, or leave those empty for an open call show and let artists submit.',
            'To assign a curator, add them in the Curators field — only artists with linked user accounts appear in this list.',
            'Save the show. The curator can then edit the show and manage its workflow.',
        ],
    },
    {
        'title': 'How to run a non-open-call show',
        'steps': [
            'Create the show (staff only) with is_open_call unchecked.',
            'Add artists and artworks directly to the show using the multi-select fields on the show edit page.',
            'Set the start and end dates. Artworks and artists become publicly visible once the start date is reached.',
            'Optionally add events (openings, talks) from the Events section.',
        ],
    },
    {
        'title': 'How to run an open call show',
        'steps': [
            'Create the show with is_open_call checked and set a submission deadline.',
            'Optionally set a decision date (informational only — it does not lock anything automatically).',
            'Artists submit their work via the show detail page while the deadline is open.',
            'After the deadline passes the show enters jury review phase automatically. Assign jurors from the show\'s Reviews page.',
            'Optionally define a rubric (weighted scoring criteria) from the Reviews page before jurors begin scoring.',
            'Jurors score each submitted artwork. View aggregated scores on the Reviews dashboard.',
            'Go to Submissions (on the show detail page) to set each submission to Selected or Rejected.',
            'Go to Promote (on the show detail page) to add all Selected artworks and their artists to the show and send acceptance/rejection emails.',
        ],
    },
    {
        'title': 'How to run a public art site open call',
        'steps': [
            'Create the show with show_type set to "Public Art Site" and enter the site location in the Location field.',
            'Enable is_open_call and set a submission deadline as you would for a regular open call.',
            'The show will display a "Public Art" badge on its card and detail page.',
            'All other open call steps (submissions, jury, promote) work the same as a gallery open call.',
        ],
    },
    {
        'title': 'How to jury a show',
        'steps': [
            'A curator or staff member will assign you as a juror for a specific show. You do not need to do anything to request this.',
            'Once assigned, sign in and go to Shows. Open the show you are jurying and click Reviews.',
            'You will see a list of submitted artworks. Click on an artwork to open the review form.',
            'If the curator has defined a rubric, you will see named criteria to score individually. Otherwise rate the artwork 1–10 overall.',
            'Add optional review notes and save. You can return to update your scores at any time.',
            'Your scores are averaged with other jurors\' scores and shown to the curator on the dashboard.',
        ],
    },
    {
        'title': 'How to link an existing artist profile to a new account',
        'steps': [
            'If you were added to the system as an artist before you had an account (e.g. a curator or staff member created your profile), sign up using the same email address that is on your artist profile.',
            'The system will automatically detect the matching email and link your new account to the existing artist profile — no duplicate will be created.',
            'If you signed up with a different email and a duplicate profile was created, email info@120710.art. A staff member can merge the two profiles and link the correct one to your account.',
        ],
    },
]

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
            'where_used': 'Accounts → Profile',
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
    'artist': {
        'title': 'Artist Guide',
        'summary': 'Artists manage their own profile and artworks, and can submit work to open call shows.',
        'allowed_actions': [
            'Create and edit your artist profile.',
            'Create and edit your own artworks.',
            'Submit artworks to open call shows via the show detail page while the deadline is open.',
            'Track your submission statuses and retract submissions from the show detail page while the deadline is open.',
            'Your artworks and profile become publicly visible once you are part of a show whose start date has passed.',
            'If you already have an artist profile in the system and your account created a new one instead of reusing it, email info@120710.art to ask a staff member to merge them.',
        ],
        'forms': [
            {
                'name': 'Artist Form',
                'where_used': 'Artist create/edit pages',
                'breadcrumb': 'Artists > New (top of page) or Artist Detail > Edit',
                'fields': [
                    {'name': 'first_name', 'input_type': 'text input', 'purpose': 'Given name for your artist profile.'},
                    {'name': 'last_name', 'input_type': 'text input', 'purpose': 'Family name for your artist profile.'},
                    {'name': 'email', 'input_type': 'email input', 'purpose': 'Contact email for curator/admin communication.'},
                    {'name': 'phone', 'input_type': 'text input', 'purpose': 'Optional contact phone number.'},
                    {'name': 'website', 'input_type': 'url/text input', 'purpose': 'External portfolio or personal website URL.'},
                    {'name': 'instagram', 'input_type': 'text input', 'purpose': 'Instagram handle starting with @.'},
                    {'name': 'venmo', 'input_type': 'text input', 'purpose': 'Venmo username starting with @.'},
                    {'name': 'bio', 'input_type': 'multi-line text area', 'purpose': 'Artist biography shown publicly.'},
                    {'name': 'statement', 'input_type': 'multi-line text area', 'purpose': 'Artist statement shown publicly.'},
                    {'name': 'image', 'input_type': 'file upload', 'purpose': 'Profile photo shown on artist pages.'},
                ],
            },
            {
                'name': 'Artwork Form',
                'where_used': 'Artwork create/edit pages',
                'breadcrumb': 'Artworks > New (top of page) or Artwork Detail > Edit',
                'fields': [
                    {'name': 'name', 'input_type': 'text input', 'purpose': 'Title of the artwork.'},
                    {'name': 'end_year', 'input_type': 'numeric input', 'purpose': 'Year the work was completed (or ending year for multi-year works).'},
                    {'name': 'start_year', 'input_type': 'numeric input', 'purpose': 'Starting year for multi-year works. Leave blank for single-year works.'},
                    {'name': 'medium', 'input_type': 'text input', 'purpose': 'Primary materials or medium.'},
                    {'name': 'width × height × depth', 'input_type': 'numeric inputs', 'purpose': 'Physical dimensions in inches. Depth is optional.'},
                    {'name': 'image', 'input_type': 'file upload', 'purpose': 'Primary artwork image. Required when creating a new artwork.'},
                    {'name': 'price', 'input_type': 'numeric input', 'purpose': 'Numeric price value when applicable.'},
                    {'name': 'pricing', 'input_type': 'text input', 'purpose': 'Price context such as NFS, POA, or edition info.'},
                    {'name': 'replacement_cost', 'input_type': 'numeric input', 'purpose': 'Insurance or replacement value.'},
                    {'name': 'is_sold', 'input_type': 'checkbox', 'purpose': 'Marks the work as sold.'},
                    {'name': 'description', 'input_type': 'multi-line text area', 'purpose': 'Public description of the work.'},
                    {'name': 'installation', 'input_type': 'multi-line text area', 'purpose': 'Display or installation notes for the venue.'},
                ],
            },
            {
                'name': 'Open Call Submission Form',
                'where_used': 'Show detail page when the show is accepting submissions',
                'breadcrumb': 'Home > Shows > Select Show > Submit Artwork',
                'fields': [
                    {'name': 'artwork', 'input_type': 'dropdown select', 'purpose': 'Choose which of your artworks to submit. Each artwork can only be submitted once per show.'},
                    {'name': 'statement', 'input_type': 'multi-line text area', 'purpose': 'Optional artist statement specific to this submission.'},
                ],
            },
        ],
    },
    'curator': {
        'title': 'Curator Guide',
        'summary': 'Curators manage shows and events, run open call workflows, assign jurors, and review aggregated jury ratings.',
        'allowed_actions': [
            'Edit and manage shows and events you are listed as curator for.',
            'See all artworks including those not yet in a show.',
            'Run open call shows: set a submission deadline, review submissions, select or reject each one, then promote selected artworks into the show.',
            'Define a weighted rubric for jury scoring: add named criteria with weights from the Manage Rubric Criteria page. Jurors score each criterion 1–10; the dashboard shows a weighted composite score per artwork.',
            'Assign artists as jurors for your show from the Reviews page.',
            'View all juror reviews and average ratings (or weighted composite scores when a rubric is defined) per artwork.',
            'Edit juror reviews when corrections are needed.',
            'Your artist profile becomes publicly visible when you are added as a curator of a show.',
            'Edit and Delete links appear only for shows and events you are assigned to curate.',
            'Artworks and artists become publicly visible once the show they are part of has opened (start date reached).',
        ],
        'forms': [
            {
                'name': 'Show Form',
                'where_used': 'Show edit pages (curators can edit, staff can create)',
                'breadcrumb': 'Shows > New (staff only) or Show Detail > Edit',
                'fields': [
                    {'name': 'name', 'input_type': 'text input', 'purpose': 'Show title shown in listings and detail pages.'},
                    {'name': 'show_type', 'input_type': 'dropdown select', 'purpose': 'Gallery Show or Public Art Site. Affects the badge shown on the show card.'},
                    {'name': 'location', 'input_type': 'text area', 'purpose': 'Address or site description. Shown on the show detail page for public art sites.'},
                    {'name': 'description', 'input_type': 'multi-line text area', 'purpose': 'Public show description.'},
                    {'name': 'image', 'input_type': 'file upload', 'purpose': 'Hero image for the show.'},
                    {'name': 'is_open_call', 'input_type': 'checkbox', 'purpose': 'Marks the show as open call enabled. Adds an Open Call badge and enables artist submissions.'},
                    {'name': 'submission_deadline', 'input_type': 'date input', 'purpose': 'Cutoff date for artist submissions. After this date the show moves to jury review phase automatically.'},
                    {'name': 'decision_date', 'input_type': 'date input', 'purpose': 'Target date for announcing selection decisions (informational only — does not lock anything automatically).'},
                    {'name': 'start', 'input_type': 'date input', 'purpose': 'Show opening date. Artworks and artists become publicly visible from this date.'},
                    {'name': 'end', 'input_type': 'date input', 'purpose': 'Show closing date.'},
                    {'name': 'tags', 'input_type': 'multi-select', 'purpose': 'Categorization tags for filtering and organization.'},
                    {'name': 'artists', 'input_type': 'multi-select', 'purpose': 'Featured artists in the show.'},
                    {'name': 'artworks', 'input_type': 'multi-select', 'purpose': 'Artworks included in the show.'},
                    {'name': 'curators', 'input_type': 'multi-select', 'purpose': 'Artists assigned as curators. They gain edit access to the show and become publicly visible.'},
                ],
            },
            {
                'name': 'Submission Status Form',
                'where_used': 'Open call submissions review page',
                'breadcrumb': 'Home > Shows > Select Show > Submissions',
                'fields': [
                    {'name': 'status per row', 'input_type': 'dropdown select per row', 'purpose': 'Set each submission to Submitted, Selected, or Rejected. Changes are saved on submit. Average juror ratings are shown alongside each entry.'},
                ],
            },
            {
                'name': 'Promote Artworks',
                'where_used': 'Open call promotion page',
                'breadcrumb': 'Home > Shows > Select Show > Promote',
                'fields': [
                    {'name': '(confirm button)', 'input_type': 'form submit', 'purpose': 'Adds all Selected artworks and their artists to the show, then sends acceptance/rejection emails to all submitting artists.'},
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
                'name': 'Juror Assignment',
                'where_used': 'Show reviews page → Manage jurors',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Manage jurors',
                'fields': [
                    {'name': 'artist', 'input_type': 'dropdown select', 'purpose': 'Choose an artist with a linked user account to assign as juror for this show. Once assigned they can log in and score submitted artworks.'},
                ],
            },
            {
                'name': 'Rubric Criteria Form',
                'where_used': 'Show reviews → Manage rubric criteria',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Manage rubric criteria',
                'fields': [
                    {'name': 'name', 'input_type': 'text input', 'purpose': 'Criterion label shown to jurors when scoring, e.g. "Originality" or "Technical execution".'},
                    {'name': 'description', 'input_type': 'multi-line text area', 'purpose': 'Optional guidance to jurors explaining what to consider for this criterion.'},
                    {'name': 'weight', 'input_type': 'numeric input', 'purpose': 'Relative importance of this criterion. Higher weight contributes more to the composite score. For example, weights of 2, 1, 1 give the first criterion twice the influence.'},
                    {'name': 'order', 'input_type': 'numeric input', 'purpose': 'Display order shown to jurors. Lower numbers appear first. Use 0, 10, 20 etc. to leave room for reordering.'},
                    {'name': 'DELETE', 'input_type': 'checkbox', 'purpose': 'Check to remove this criterion. Deleting a criterion also removes all juror scores for it.'},
                ],
            },
            {
                'name': 'Curator Review Edit Form',
                'where_used': 'Artwork reviews detail (curator view)',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Select Artwork > Edit Review',
                'fields': [
                    {'name': 'rating', 'input_type': 'radio select (1–10)', 'purpose': 'Adjust juror score when corrections are required.'},
                    {'name': 'body', 'input_type': 'multi-line text area', 'purpose': 'Adjust juror note text when needed.'},
                ],
            },
        ],
    },
    'juror': {
        'title': 'Juror Guide',
        'summary': 'Jurors review and score artworks for the specific shows they have been assigned to.',
        'allowed_actions': [
            'Access the Reviews page for shows you have been assigned to as juror.',
            'Score each submitted artwork in your assigned show. You can return to update your scores at any time.',
            'If the curator has defined a rubric, score each named criterion individually. Otherwise give an overall 1–10 rating.',
            'Optionally add qualitative review notes alongside your score.',
        ],
        'forms': [
            {
                'name': 'Artwork Review Form',
                'where_used': 'Show review workflow for assigned jurors',
                'breadcrumb': 'Home > Shows > Select Show > Reviews > Select Artwork',
                'fields': [
                    {'name': 'criterion scores', 'input_type': 'radio select (1–10) per criterion', 'purpose': 'Score each rubric criterion if the curator has defined one. All criteria are required.'},
                    {'name': 'rating', 'input_type': 'radio select (1–10)', 'purpose': 'Overall score when no rubric is defined. Optional when criteria are present.'},
                    {'name': 'body', 'input_type': 'multi-line text area', 'purpose': 'Optional qualitative review notes visible to the curator.'},
                ],
            },
        ],
    },
    'staff': {
        'title': 'Staff Guide',
        'summary': 'Staff have full access across all shows, artists, artworks, and review workflows.',
        'allowed_actions': [
            'All curator capabilities across all shows and events.',
            'Create new shows (only staff can create; curators can edit assigned shows).',
            'Assign artists as curators of shows via the Show Edit page — this grants them edit access and makes their artist profile publicly visible.',
            'Assign jurors to any show from the show\'s Reviews page.',
            'View and edit juror reviews across all shows.',
            'Edit and Delete links are shown only for records you can manage.',
        ],
        'forms': [
            {
                'name': 'Artwork Form (Staff Controls)',
                'where_used': 'Artwork create/edit pages',
                'breadcrumb': 'Artworks > New (top of page) or Artwork Detail > Edit',
                'fields': [
                    {'name': 'artists', 'input_type': 'multi-select', 'purpose': 'Associate one or more artists with the work.'},
                    {'name': 'shows', 'input_type': 'multi-select', 'purpose': 'Associate the artwork with one or more shows. Artwork becomes publicly visible once added to a show whose start date has passed.'},
                    {'name': 'tags', 'input_type': 'multi-select', 'purpose': 'Internal or public categorization tags.'},
                ],
            },
            {
                'name': 'Show Form (Staff Fields)',
                'where_used': 'Show create/edit pages',
                'breadcrumb': 'Shows > New (top of page) or Show Detail > Edit',
                'fields': [
                    {'name': 'curators', 'input_type': 'multi-select', 'purpose': 'Assign artists as curators. They gain edit access to the show and become publicly visible.'},
                    {'name': 'show_type', 'input_type': 'dropdown select', 'purpose': 'Gallery Show or Public Art Site. Affects the badge and display on show cards.'},
                    {'name': 'location', 'input_type': 'text area', 'purpose': 'Address or site description for public art installations.'},
                ],
            },
        ],
    },
}
