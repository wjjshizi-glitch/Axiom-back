import time
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from automation.models import TestRun
from automation.runner import execute_run


class Command(BaseCommand):
    help = 'Consume durable test jobs. Use one worker with SQLite.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        self.stdout.write('Axiom worker ready')
        try:
            while True:
                close_old_connections()
                TestRun.objects.filter(status='running', heartbeat_at__lt=timezone.now() - timedelta(
                    seconds=max(settings.RUNNER_TIMEOUT * 2, 120))).update(
                        status='error', finished_at=timezone.now(), log='Worker heartbeat lost; job not retried automatically.')
                run = TestRun.objects.filter(status='queued', cancel_requested=False).order_by('id').first()
                if run:
                    now = timezone.now()
                    claimed = TestRun.objects.filter(pk=run.pk, status='queued', cancel_requested=False).update(
                        status='running', started_at=now, heartbeat_at=now)
                    if claimed:
                        run.refresh_from_db()
                        execute_run(run)
                        self.stdout.write(f'Run #{run.pk}: {run.status}')
                if options['once']:
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write('Worker stopped')
