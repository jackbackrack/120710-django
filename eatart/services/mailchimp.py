from django.conf import settings
from mailchimp_marketing import Client


def subscribe_to_mailing_list(*, first_name, last_name, email):
    mailchimp_client = Client()
    mailchimp_client.set_config({
        'api_key': settings.MAILCHIMP_API_KEY,
        'server': settings.MAILCHIMP_DATA_CENTER,
    })

    user_info = {
        'email_address': email,
        'status': 'subscribed',
        'merge_fields': {
            'FNAME': first_name,
            'LNAME': last_name,
        },
    }

    mailchimp_client.lists.add_list_member(settings.MAILCHIMP_AUDIENCE_ID, user_info)
