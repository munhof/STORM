"""Template context shared by Studio's pages."""
from django.conf import settings


def studio_brand(_request):
    return {
        'studio_brand_name': settings.STUDIO_BRAND_NAME,
        'studio_brand_tagline': settings.STUDIO_BRAND_TAGLINE,
    }
