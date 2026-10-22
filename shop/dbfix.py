"""
Bazani avtomatik tuzatish. `py manage.py runbot` har safar ishga tushganda chaqiradi.

1) Odatiy yo'l: makemigrations + migrate (ma'lumotlar saqlanadi).
2) Agar baza hali ham modellarga mos kelmasa (eski/buzilgan migratsiyalar):
   baza faylining ZAXIRA nusxasi olinadi, faqat `shop_*` jadvallari qayta yaratiladi
   (admin foydalanuvchisi va Django jadvallari saqlanadi).
"""
import datetime
import importlib
import shutil
import sys
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.db import connection

APP = "shop"


def _models():
    return list(apps.get_app_config(APP).get_models())


def schema_ok() -> bool:
    tables = set(connection.introspection.table_names())
    for model in _models():
        table = model._meta.db_table
        if table not in tables:
            return False
        with connection.cursor() as cur:
            cols = {c.name for c in connection.introspection.get_table_description(cur, table)}
        if cols != {f.column for f in model._meta.local_concrete_fields}:
            return False
    return True


def _forget_migration_modules():
    for name in [n for n in sys.modules if n.startswith(f"{APP}.migrations.")]:
        del sys.modules[name]
    importlib.invalidate_caches()


def reset_shop():
    db_name = settings.DATABASES["default"]["NAME"]
    db_path = Path(str(db_name))
    connection.close()
    if db_path.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = db_path.with_name(f"{db_path.stem}_backup_{stamp}{db_path.suffix}")
        shutil.copy2(db_path, backup)
        print(f"[dbfix] Zaxira nusxa: {backup.name}")

    with connection.cursor() as cur:
        cur.execute("PRAGMA foreign_keys = OFF")
        names = connection.introspection.table_names()
        for t in [n for n in names if n.startswith(f"{APP}_")]:
            cur.execute(f'DROP TABLE IF EXISTS "{t}"')
        if "django_migrations" in names:
            cur.execute("DELETE FROM django_migrations WHERE app = %s", [APP])
        cur.execute("PRAGMA foreign_keys = ON")

    mig_dir = Path(settings.BASE_DIR) / APP / "migrations"
    mig_dir.mkdir(exist_ok=True)
    (mig_dir / "__init__.py").touch()
    for f in mig_dir.glob("0*.py"):
        f.unlink()
    shutil.rmtree(mig_dir / "__pycache__", ignore_errors=True)
    _forget_migration_modules()

    call_command("makemigrations", APP, interactive=False, verbosity=0)
    call_command("migrate", interactive=False, verbosity=0)


def ensure_db():
    failed = False
    try:
        call_command("makemigrations", APP, interactive=False, verbosity=0)
        call_command("migrate", interactive=False, verbosity=0)
    except (Exception, SystemExit) as ex:
        failed = True
        print(f"[dbfix] Migratsiyada muammo: {ex}")

    if not schema_ok():
        print("[dbfix] Baza tuzilishi mos emas: shop jadvallari qayta yaratilmoqda (zaxira bilan)...")
        reset_shop()
        if not schema_ok():
            raise RuntimeError("Bazani tuzatib bo'lmadi. db.sqlite3 va shop/migrations ni qo'lda o'chirib ko'ring.")
        print("[dbfix] Tayyor.")
    elif failed:
        try:  # jadvallar to'g'ri, faqat migratsiya yozuvi yetishmaydi
            call_command("migrate", APP, fake=True, interactive=False, verbosity=0)
        except (Exception, SystemExit):
            pass

    from shop.models import Plan
    if not Plan.objects.filter(is_active=True).exists():
        call_command("seed_plans")
