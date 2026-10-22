import asyncio
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from shop.services import ai


class Command(BaseCommand):
    help = "AI ulanishini tekshiradi va aniq sababni ko'rsatadi"

    def handle(self, *args, **options):
        out = self.stdout.write
        out(f"Python: {sys.version.split()[0]}")
        env = Path(settings.BASE_DIR) / ".env"
        out(f".env fayli: {'topildi' if env.exists() else 'TOPILMADI'} ({env})")
        problem = ai.config_problem()
        if problem:
            out(self.style.ERROR(f"MUAMMO: {problem}"))
            return
        key = ai.active_key()
        out(f"Provayder: {ai.provider_name()}")
        out(f"Kalit: {key[:7]}...{key[-4:]} (uzunligi {len(key)})")
        if ai.key_warning():
            out(self.style.WARNING(ai.key_warning()))
        if ai.provider() == "anthropic":
            out(f"Workspace ID: {ai.workspace_id() or 'kiritilmagan'}")
        out(f"Modellar: {', '.join(ai.active_models())}")
        if ai.provider() == "groq":
            try:
                found = asyncio.run(ai.list_groq_models())
                out("Groq hisobidagi modellar: " + (", ".join(found[:8]) or "ro'yxat bo'sh"))
            except ai.AIError as ex:
                out(self.style.WARNING(f"Modellar ro'yxatini olib bo'lmadi: {ex.reason}"))
        out("Sinov so'rovi yuborilmoqda...")
        try:
            text = asyncio.run(ai.generate([{"role": "user", "content": "Faqat OK deb javob ber."}], max_tokens=20))
            out(self.style.SUCCESS(f"AI ISHLAYAPTI. Javob: {text[:100]}"))
        except ai.AIError as ex:
            out(self.style.ERROR(f"XATO: {ex.reason}"))
