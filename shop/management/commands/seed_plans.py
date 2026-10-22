from django.core.management.base import BaseCommand
from shop.models import Plan

PLANS = [
    ("1 oylik Premium", 30, 100000),
    ("3 oylik Premium", 90, 500000),
]


class Command(BaseCommand):
    help = "Standart tariflarni yaratadi: 1 oy = 100 000, 3 oy = 500 000 so'm"

    def handle(self, *args, **options):
        names = []
        for name, days, price in PLANS:
            Plan.objects.update_or_create(
                name=name,
                defaults={"duration_days": days, "price_uzs": price, "is_active": True,
                          "description": f"FitAI Premium: {days} kun"},
            )
            names.append(name)
        Plan.objects.exclude(name__in=names).update(is_active=False)
        self.stdout.write(self.style.SUCCESS("Tariflar tayyor: " + ", ".join(names)))
