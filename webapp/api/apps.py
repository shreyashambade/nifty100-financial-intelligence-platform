from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = 'api'

    def ready(self):
        try:
            import api.authentication  # Ensure drf-spectacular authentication extensions are registered
        except ImportError:
            pass
