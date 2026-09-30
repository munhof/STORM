import os
from pathlib import Path

WORKSPACE = Path(os.environ.get('STORM_WORKSPACE', '.storm')).resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
secret_path = WORKSPACE / 'secret.key'
if not secret_path.exists():
    import secrets
    try:
        with secret_path.open('x', encoding='utf-8') as stream:
            stream.write(secrets.token_urlsafe(48))
        secret_path.chmod(0o600)
    except FileExistsError:
        pass
SECRET_KEY = secret_path.read_text().strip()
DEBUG = False
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]']
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes',
                  'django.contrib.sessions', 'django.contrib.messages',
                  'django.contrib.staticfiles', 'storm_studio']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware',
              'django.contrib.sessions.middleware.SessionMiddleware',
              'django.middleware.common.CommonMiddleware',
              'django.middleware.csrf.CsrfViewMiddleware',
              'django.contrib.auth.middleware.AuthenticationMiddleware',
              'django.contrib.messages.middleware.MessageMiddleware']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
                         'NAME': WORKSPACE / 'studio.sqlite3', 'OPTIONS': {'timeout': 20}}}
ARTIFACT_ROOT = WORKSPACE / 'artifacts'
ROOT_URLCONF = 'storm_studio.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
              'APP_DIRS': True, 'OPTIONS': {'context_processors': [
                  'django.template.context_processors.request',
                  'django.contrib.messages.context_processors.messages',
                  'storm_studio.context_processors.studio_brand']}}]
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
STATIC_URL = '/static/'
USE_TZ = True
LANGUAGE_CODE = 'es'
STORM_PLUGINS = tuple(filter(None, os.environ.get('STORM_PLUGINS', '').split(',')))
STUDIO_BRAND_NAME = os.environ.get('STUDIO_BRAND_NAME', 'STORM Studio').strip() or 'STORM Studio'
STUDIO_BRAND_TAGLINE = os.environ.get(
    'STUDIO_BRAND_TAGLINE', 'Estudios científicos reproducibles').strip()
