"""
ASGI config for eatart project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eatart.settings')

from django.core.asgi import get_asgi_application
from starlette.applications import Starlette
from starlette.routing import Mount

django_application = get_asgi_application()

from eatart.api.app import api

application = Starlette(routes=[
	Mount('/api', app=api),
	Mount('/', app=django_application),
])
