import os
from celery import Celery

# Set default Django settings module for 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nifty_core.settings')

app = Celery('nifty_core', broker='redis://127.0.0.1:6379/0')

# Load configuration from Django settings, namespace='CELERY' means
# all celery-related configuration keys should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Automatically discover tasks from all registered Django apps (e.g. api.tasks).
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Celery debug request: {self.request!r}')
