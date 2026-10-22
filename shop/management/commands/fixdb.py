from django.core.management.base import BaseCommand
from shop.dbfix import ensure_db


class Command(BaseCommand):
    help = "Bazani modellarga moslaydi (migratsiya + kerak bo'lsa shop jadvallarini qayta yaratadi)"

    def handle(self, *args, **options):
        ensure_db()
        self.stdout.write(self.style.SUCCESS("Baza tayyor."))
