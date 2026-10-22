import asyncio
from django.core.management.base import BaseCommand
from shop.bot import run_bot
from shop.dbfix import ensure_db
from shop.services import ai


class Command(BaseCommand):
    help = "FitAI Telegram botni ishga tushiradi (bazani avtomatik tuzatadi)"

    def handle(self, *args, **options):
        ensure_db()
        problem = ai.config_problem()
        if problem:
            self.stdout.write("AI: MUAMMO: " + problem)
        else:
            self.stdout.write(f"AI: {ai.provider_name()} kaliti topildi (tekshirish: Telegram'da /aitest)")
        asyncio.run(run_bot())
