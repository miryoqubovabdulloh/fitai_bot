import logging
import re
from html import escape, unescape

from asgiref.sync import sync_to_async
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError,
                                TelegramUnauthorizedError)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from shop.content import DISCLAIMER, GOALS, MEALS, WORKOUTS
from shop.emojis import EMOJIS, e, emoji_id, plain, set_emoji_id
from shop.models import AIMessage, Payment, Plan, Subscriber, SupportMessage
from shop.services import ai
from shop.services.subscriptions import activate_payment, reject_payment

router = Router()
AI_LIMIT = int(getattr(settings, "AI_DAILY_LIMIT", 30))
STATE = {"icons_ok": True}  # Telegram tugma ikonkalarini rad etsa, False bo'ladi


class BMI(StatesGroup):
    data = State()


class Cal(StatesGroup):
    gender = State()
    data = State()
    activity = State()


# ---------- DB ----------
@sync_to_async
def get_or_create_subscriber(user):
    sub, _ = Subscriber.objects.get_or_create(telegram_id=user.id)
    sub.username = user.username or ""
    sub.full_name = user.full_name
    sub.save()
    return sub


@sync_to_async
def get_subscriber(telegram_id):
    return Subscriber.objects.filter(telegram_id=telegram_id).first()


@sync_to_async
def list_plans():
    return list(Plan.objects.filter(is_active=True))


@sync_to_async
def get_plan(plan_id):
    return Plan.objects.filter(pk=plan_id, is_active=True).first()


@sync_to_async
def list_payments(telegram_id):
    return list(Payment.objects.filter(subscriber__telegram_id=telegram_id)
                .select_related("plan").order_by("-id")[:10])


@sync_to_async
def create_payment(sub, plan):
    # foydalanuvchining chek yuborilmagan eski so'rovlarini tozalaymiz
    Payment.objects.filter(subscriber=sub, status=Payment.Status.PENDING).delete()
    return Payment.objects.create(subscriber=sub, plan=plan, method=Payment.Method.CARD,
                                  amount=plan.price_uzs, currency="UZS")


@sync_to_async
def last_pending_card_payment(telegram_id):
    return Payment.objects.filter(
        subscriber__telegram_id=telegram_id, method=Payment.Method.CARD, status=Payment.Status.PENDING
    ).select_related("plan", "subscriber").order_by("-id").first()


@sync_to_async
def get_payment_admin(payment_id):
    return Payment.objects.select_related("plan", "subscriber").filter(pk=payment_id).first()


@sync_to_async
def list_review_payments():
    return list(Payment.objects.filter(status=Payment.Status.REVIEW)
                .select_related("plan", "subscriber").order_by("id")[:15])


@sync_to_async
def has_review_payment(telegram_id):
    return Payment.objects.filter(subscriber__telegram_id=telegram_id, status=Payment.Status.REVIEW).exists()


@sync_to_async
def admin_stats():
    now = timezone.now()
    paid = Payment.objects.filter(status=Payment.Status.PAID)
    income = {r["currency"]: r["s"] for r in paid.values("currency").annotate(s=Sum("amount"))}
    return {
        "users": Subscriber.objects.count(),
        "premium": Subscriber.objects.filter(premium_until__gt=now).count(),
        "review": Payment.objects.filter(status=Payment.Status.REVIEW).count(),
        "paid": paid.count(),
        "income": income,
    }


@sync_to_async
def save_profile(sub, profile):
    sub.profile = profile
    sub.save(update_fields=["profile"])


@sync_to_async
def ai_history(sub, limit=12):
    rows_ = list(AIMessage.objects.filter(subscriber=sub, archived=False).order_by("-id")[:limit])[::-1]
    while rows_ and rows_[0].role != "user":
        rows_.pop(0)
    return [{"role": r.role, "content": r.content} for r in rows_]


@sync_to_async
def save_ai_pair(sub, user_text, answer):
    AIMessage.objects.create(subscriber=sub, role="user", content=user_text)
    AIMessage.objects.create(subscriber=sub, role="assistant", content=answer)


@sync_to_async
def count_ai_today(sub):
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return AIMessage.objects.filter(subscriber=sub, role="user", created_at__gte=start).count()


@sync_to_async
def archive_ai_history(sub):
    AIMessage.objects.filter(subscriber=sub, archived=False).update(archived=True)


@sync_to_async
def create_support(sub, text, kind):
    return SupportMessage.objects.create(subscriber=sub, text=text, kind=kind)


@sync_to_async
def get_support(ticket_id):
    if not ticket_id:
        return None
    return SupportMessage.objects.select_related("subscriber").filter(pk=ticket_id).first()


@sync_to_async
def save_suggestion(ticket, text):
    ticket.suggested = text
    ticket.save(update_fields=["suggested"])


@sync_to_async
def mark_support(ticket, status, answer=""):
    ticket.status = status
    fields = ["status"]
    if answer:
        ticket.answer = answer
        ticket.answered_at = timezone.now()
        fields += ["answer", "answered_at"]
    ticket.save(update_fields=fields)


@sync_to_async
def count_support_today(sub):
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return SupportMessage.objects.filter(subscriber=sub, created_at__gte=start).count()


@sync_to_async
def support_context(sub):
    plans = Plan.objects.filter(is_active=True)
    plan_lines = "\n".join(f"- {p.name}: {p.price_uzs:,} so'm, {p.duration_days} kun" for p in plans)
    pays = Payment.objects.filter(subscriber=sub).select_related("plan").order_by("-id")[:3]
    pay_lines = "\n".join(f"- #{p.id} {p.plan.name}: {p.get_status_display()}" for p in pays) or "- yo'q"
    if sub.is_premium:
        status = f"Premium {sub.premium_until:%d.%m.%Y} gacha"
    else:
        status = "Premium emas"
    return (f"Tariflar:\n{plan_lines}\n\n"
            f"To'lov kartasi: {settings.CARD_NUMBER} ({settings.CARD_HOLDER})\n"
            f"Foydalanuvchi: {sub.full_name}, {status}\n"
            f"So'nggi to'lovlari:\n{pay_lines}")


@sync_to_async
def attach_receipt(payment, file_id):
    payment.receipt_file_id = file_id
    payment.status = Payment.Status.REVIEW
    payment.save(update_fields=["receipt_file_id", "status"])


# ---------- Tugmalar va ko'rsatish ----------
def kbtn(text, data=None, emoji=None, url=None):
    """Inline tugma. emoji berilsa va premium ID bo'lsa — tugma ikonkasi, bo'lmasa oddiy emoji."""
    kw = {"text": text}
    if data:
        kw["callback_data"] = data
    if url:
        kw["url"] = url
    if emoji:
        cid = emoji_id(emoji)
        if STATE["icons_ok"] and cid:
            kw["icon_custom_emoji_id"] = cid
        else:
            kw["text"] = f"{plain(emoji)} {text}"
    return InlineKeyboardButton(**kw)


def rows(*r):
    return InlineKeyboardMarkup(inline_keyboard=[list(x) for x in r])


def back_kb(target="m:home", label="⬅️ Orqaga"):
    return rows([kbtn(label, target)])


def home_kb(is_premium, is_admin=False):
    lock = "" if is_premium else " 🔒"
    r = [
        [kbtn(f"AI Murabbiy{lock}", "m:ai", "ai")],
        [kbtn("Premium sotib olish", "m:premium", "premium"), kbtn("Profilim", "m:profile", "profile")],
        [kbtn(f"Mashqlar{lock}", "m:workouts", "workout"), kbtn(f"Ovqatlanish{lock}", "m:meals", "food")],
        [kbtn(f"Kaloriya{lock}", "m:cal", "fire"), kbtn("BMI", "m:bmi", "bmi")],
        [kbtn("Tarix", "m:history", "history"), kbtn("Yordam", "m:help", "info")],
        [kbtn("Adminga yozish", "m:support", "support")],
    ]
    if is_admin:
        r.append([kbtn("🛠 Admin panel", "adm:home")])
    return rows(*r)


def lock_kb():
    return rows([kbtn("Premium olish", "m:premium", "premium")], [kbtn("⬅️ Orqaga", "m:home")])


def goals_kb(prefix):
    r = [[kbtn(t, f"{prefix}:{k}")] for k, t in GOALS.items()]
    r.append([kbtn("⬅️ Orqaga", "m:home")])
    return rows(*r)


def plans_kb(plans):
    r = [[kbtn(f"{p.name} — {p.price_uzs:,} so'm", f"plan:{p.id}", "premium")]
         for p in plans]
    r.append([kbtn("⬅️ Orqaga", "m:home")])
    return rows(*r)


async def render(target: Message, text: str, kb_fn, edit=False):
    """Xabarni yuboradi/tahrirlaydi. Tugma ikonkasi rad etilsa, oddiy emojiga o'tadi."""
    for _ in range(3):
        try:
            if edit:
                await target.edit_text(text, reply_markup=kb_fn())
            else:
                await target.answer(text, reply_markup=kb_fn())
            return
        except TelegramBadRequest as ex:
            if "not modified" in str(ex):
                return
            if STATE["icons_ok"] and any(emoji_id(k) for k in EMOJIS):
                STATE["icons_ok"] = False
                logging.warning("Tugma ikonkalari rad etildi (%s). Oddiy emoji ishlatiladi.", ex)
                continue
            if edit:
                edit = False
                continue
            raise


def status_line(sub) -> str:
    if sub and sub.is_premium:
        days = (sub.premium_until - timezone.now()).days
        return (f"{e('premium')} Status: <b>PREMIUM</b> {e('check')}\n"
                f"{e('clock')} Tugash: {sub.premium_until:%d.%m.%Y} ({days} kun qoldi)")
    return f"{e('free')} Status: <b>Oddiy</b> (Premium emas)"


def home_text(name, sub) -> str:
    rank = f"{e('gold')} Gold" if sub.is_premium else f"{e('bronze')} Bronze"
    return (
        f"{e('star')} Salom, <b>{escape(name)}</b>! {rank}\n\n"
        f"{e('premium')} <b>FITAI PREMIUM CLUB</b>\n\n"
        "Bu yerda siz:\n"
        f"{e('workout')} Haftalik mashq dasturlari\n"
        f"{e('food')} Ovqatlanish menyulari\n"
        f"{e('fire')} Kaloriya va BMI hisoblash\n\n"
        "tez, qulay va xavfsiz to'lov bilan\n\n"
        f"{status_line(sub)}"
    )


def parse_numbers(text, n):
    nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", text or "")]
    return nums if len(nums) == n else None


def is_admin(user_id) -> bool:
    return user_id in settings.ADMIN_IDS


def card_block() -> str:
    return (f"{e('card')} <b>To'lov kartasi</b>\n"
            f"<code>{escape(settings.CARD_NUMBER)}</code>\n"
            f"{e('profile')} {escape(settings.CARD_HOLDER)}")


def fmt_amount(p) -> str:
    return f"{p.amount:,.0f} {p.currency}" if p.currency == "UZS" else f"{p.amount:.2f} {p.currency}"


def receipt_caption(p) -> str:
    sub = p.subscriber
    uname = f" (@{escape(sub.username)})" if sub.username else ""
    return (f"🧾 <b>Yangi chek</b> #{p.id}\n\n"
            f"👤 {escape(sub.full_name or str(sub.telegram_id))}{uname}\n"
            f"🆔 <code>{sub.telegram_id}</code>\n"
            f"📦 {escape(p.plan.name)} ({p.plan.duration_days} kun)\n"
            f"💰 <b>{fmt_amount(p)}</b>\n\n{card_block()}")


def review_kb(pid):
    return rows([kbtn("✅ Tasdiqlash", f"adm:ok:{pid}"), kbtn("❌ Rad etish", f"adm:no:{pid}")])


STATUS_ICON = {"paid": "✅", "review": "⏳", "pending": "🕓", "rejected": "❌"}


# ---------- Admin: premium emoji ID ----------
def has_custom_emoji(m: Message) -> bool:
    return any(x.type == "custom_emoji" for x in (m.entities or []))


@router.message(Command("emojilar"), F.from_user.id.in_(settings.ADMIN_IDS))
async def emoji_list(message: Message):
    lines = [f"{'✅' if emoji_id(k) else '▫️'} <code>{k}</code> {plain(k)}" for k in EMOJIS]
    await message.answer(
        "<b>Emoji nomlari</b> (✅ = premium ID o'rnatilgan):\n\n" + "\n".join(lines) +
        "\n\nO'rnatish: <code>nom</code> va premium emojini birga yuboring, masalan: <code>premium</code> 💎"
    )


@router.message(has_custom_emoji, F.from_user.id.in_(settings.ADMIN_IDS))
async def emoji_ids(message: Message):
    text = message.text or ""
    u16 = text.encode("utf-16-le")
    saved, ids = [], []
    for ent in message.entities:
        if ent.type != "custom_emoji":
            continue
        ids.append(ent.custom_emoji_id)
        prefix = u16[: ent.offset * 2].decode("utf-16-le")
        words = prefix.split("\n")[-1].split()
        key = words[-1].lower() if words else ""
        if key in EMOJIS:
            set_emoji_id(key, ent.custom_emoji_id)
            saved.append(key)
    if saved:
        STATE["icons_ok"] = True
        await message.answer(f"✅ Saqlandi: {', '.join(saved)}\nBotni qayta ishga tushirish shart emas. /start bosib ko'ring.")
    else:
        lines = "\n".join(f"<code>{i}</code>" for i in ids)
        await message.answer(f"Emoji ID lar:\n{lines}\n\nNom bilan yuboring, masalan: <code>premium</code> + emoji. Nomlar: /emojilar")


# ---------- Bosh menyu ----------
@router.message(CommandStart())
@router.message(Command("menu"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    sub = await get_or_create_subscriber(message.from_user)
    tmp = await message.answer("⏳", reply_markup=ReplyKeyboardRemove())  # eski pastki tugmalarni olib tashlash
    try:
        await tmp.delete()
    except TelegramBadRequest:
        pass
    await render(message, home_text(message.from_user.first_name, sub), lambda: home_kb(sub.is_premium, is_admin(message.from_user.id)))


@router.callback_query(F.data.startswith("m:"))
async def menu_cb(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    key = cb.data[2:]
    sub = await get_or_create_subscriber(cb.from_user)
    msg = cb.message

    if key == "home":
        await render(msg, home_text(cb.from_user.first_name, sub), lambda: home_kb(sub.is_premium, is_admin(cb.from_user.id)), edit=True)

    elif key == "premium":
        plans = await list_plans()
        if sub.is_premium:
            text = f"{e('crown')} <b>Siz Premium foydalanuvchisiz!</b>\n\n{status_line(sub)}\n\nMuddatni uzaytirish:"
        else:
            text = (f"{e('premium')} <b>FitAI Premium</b> imkoniyatlari:\n\n"
                    f"{e('check')} {e('workout')} Haftalik mashq dasturlari\n"
                    f"{e('check')} {e('food')} Ovqatlanish menyulari\n"
                    f"{e('check')} {e('fire')} Kaloriya kalkulyatori\n"
                    f"{e('check')} {e('ai')} AI Murabbiy: shaxsiy reja va savol-javob\n"
                    f"{e('check')} {e('crown')} Premium status\n\nTarifni tanlang:")
        text += f"\n\n{card_block()}"
        if not plans:
            text += "\n\nHozircha tariflar mavjud emas."
        await render(msg, text, lambda: plans_kb(plans), edit=True)

    elif key == "profile":
        text = (f"{e('profile')} <b>Profilim</b>\n\nIsm: {escape(sub.full_name)}\n"
                f"ID: <code>{sub.telegram_id}</code>\n\n{status_line(sub)}")
        if not sub.is_premium:
            text += f"\n\n{e('lock')} Yopiq bo'limlar: Mashqlar, Ovqatlanish, Kaloriya."
        await render(msg, text, back_kb, edit=True)

    elif key in ("workouts", "meals"):
        if not sub.is_premium:
            await render(msg, f"{e('lock')} Bu bo'lim faqat <b>Premium</b> uchun.", lock_kb, edit=True)
        elif key == "workouts":
            await render(msg, f"{e('workout')} Maqsadingizni tanlang:", lambda: goals_kb("wo"), edit=True)
        else:
            await render(msg, f"{e('food')} Maqsadingizni tanlang:", lambda: goals_kb("meal"), edit=True)

    elif key == "bmi":
        await state.set_state(BMI.data)
        await render(msg, f"{e('bmi')} Bo'yingiz (sm) va vazningiz (kg) ni yozing.\nMasalan: <code>175 70</code>",
                     back_kb, edit=True)

    elif key == "cal":
        if not sub.is_premium:
            await render(msg, f"{e('lock')} Bu bo'lim faqat <b>Premium</b> uchun.", lock_kb, edit=True)
        else:
            await state.set_state(Cal.gender)
            await render(msg, f"{e('fire')} Jinsingizni tanlang:",
                         lambda: rows([kbtn("👨 Erkak", "cal_g:m"), kbtn("👩 Ayol", "cal_g:f")],
                                      [kbtn("⬅️ Orqaga", "m:home")]), edit=True)

    elif key == "history":
        pays = await list_payments(cb.from_user.id)
        if not pays:
            text = f"{e('history')} <b>Tarix</b>\n\nHali to'lovlar yo'q."
        else:
            lines = []
            for p in pays:
                amt = f"{p.amount:,.0f}" if p.currency == "UZS" else f"{p.amount:.2f}"
                lines.append(f"{STATUS_ICON.get(p.status, '•')} #{p.id} • {escape(p.plan.name)} • "
                             f"{amt} {p.currency} • {timezone.localtime(p.created_at):%d.%m.%Y}")
            text = f"{e('history')} <b>So'nggi to'lovlar</b>\n\n" + "\n".join(lines)
        await render(msg, text, back_kb, edit=True)

    elif key == "ai":
        if not sub.is_premium:
            await render(msg, f"{e('lock')} Bu bo'lim faqat <b>Premium</b> uchun.", lock_kb, edit=True)
        else:
            await show_ai_menu(msg, sub)

    elif key in ("support", "support_new"):
        await state.set_state(Support.write)
        await render(msg,
                     f"{e('support')} <b>Adminga yozish</b>\n\n"
                     "Savol yoki muammoingizni yozing (matn, rasm yoki skrinshot). Admin javobi shu yerda keladi.",
                     back_kb, edit=(key == "support"))

    elif key == "help":
        await render(msg,
                     f"{e('info')} <b>Yordam</b>\n\n"
                     f"{e('bmi')} BMI: bepul\n"
                     f"{e('workout')} Mashqlar, {e('food')} Ovqatlanish, {e('fire')} Kaloriya, {e('ai')} AI Murabbiy: Premium\n"
                     f"{e('card')} To'lov karta orqali, chek admin tomonidan tekshiriladi.\n"
                     f"{card_block()}\n\nSavollar uchun admin bilan bog'laning.", back_kb, edit=True)
    await cb.answer()


# ---------- Mashq / Ovqatlanish ----------
@router.callback_query(F.data.func(lambda d: d.startswith(("wo:", "meal:"))))
async def show_content(cb: CallbackQuery):
    sub = await get_subscriber(cb.from_user.id)
    if not (sub and sub.is_premium):
        await cb.answer("Faqat Premium uchun", show_alert=True)
        return
    kind, goal = cb.data.split(":")
    text = (WORKOUTS if kind == "wo" else MEALS)[goal] + DISCLAIMER
    await render(cb.message, text, lambda: back_kb("m:workouts" if kind == "wo" else "m:meals"), edit=True)
    await cb.answer()


# ---------- BMI ----------
@router.message(BMI.data)
async def bmi_calc(message: Message, state: FSMContext):
    nums = parse_numbers(message.text, 2)
    if not nums or not (100 <= nums[0] <= 250 and 30 <= nums[1] <= 300):
        await message.answer("Noto'g'ri format. Masalan: <code>175 70</code>")
        return
    h, w = nums[0] / 100, nums[1]
    bmi = w / (h * h)
    cat = ("Vazn yetishmaydi" if bmi < 18.5 else "Normal ✅" if bmi < 25
           else "Ortiqcha vazn" if bmi < 30 else "Semizlik")
    await state.clear()
    await render(message,
                 f"{e('bmi')} <b>BMI: {bmi:.1f}</b>\nNatija: {cat}\n"
                 f"Sog'lom vazn oralig'i: {18.5 * h * h:.0f}–{24.9 * h * h:.0f} kg\n\n"
                 "<i>BMI taxminiy ko'rsatkich.</i>",
                 lambda: back_kb("m:home", "⬅️ Menyu"))


# ---------- Kaloriya ----------
@router.callback_query(Cal.gender, F.data.startswith("cal_g:"))
async def cal_gender(cb: CallbackQuery, state: FSMContext):
    await state.update_data(gender=cb.data.split(":")[1])
    await state.set_state(Cal.data)
    await render(cb.message, "Yosh, bo'y (sm) va vazn (kg) ni yozing.\nMasalan: <code>25 175 70</code>",
                 back_kb, edit=True)
    await cb.answer()


@router.message(Cal.data)
async def cal_data(message: Message, state: FSMContext):
    nums = parse_numbers(message.text, 3)
    if not nums or not (10 <= nums[0] <= 100 and 100 <= nums[1] <= 250 and 30 <= nums[2] <= 300):
        await message.answer("Noto'g'ri format. Masalan: <code>25 175 70</code>")
        return
    await state.update_data(age=nums[0], height=nums[1], weight=nums[2])
    await state.set_state(Cal.activity)
    await render(message, "Faollik darajangizni tanlang:", lambda: rows(
        [kbtn("Kam harakat", "cal_a:1.2")],
        [kbtn("Yengil (haftada 1–3 mashq)", "cal_a:1.375")],
        [kbtn("O'rtacha (3–5 mashq)", "cal_a:1.55")],
        [kbtn("Yuqori (6–7 mashq)", "cal_a:1.725")],
        [kbtn("⬅️ Orqaga", "m:home")]))


@router.callback_query(Cal.activity, F.data.startswith("cal_a:"))
async def cal_result(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    act = float(cb.data.split(":")[1])
    bmr = 10 * d["weight"] + 6.25 * d["height"] - 5 * d["age"] + (5 if d["gender"] == "m" else -161)
    tdee = bmr * act
    await state.clear()
    await render(cb.message,
                 f"{e('fire')} <b>Kunlik kaloriya (taxminiy)</b>\n\n"
                 f"Saqlash: <b>{tdee:.0f}</b> kkal\n"
                 f"Ozish uchun: ~{tdee * 0.85:.0f} kkal\n"
                 f"Massa uchun: ~{tdee * 1.10:.0f} kkal\n\n"
                 "<i>Mifflin-St Jeor formulasi. Natija individual farq qilishi mumkin.</i>",
                 lambda: back_kb("m:home", "⬅️ Menyu"), edit=True)
    await cb.answer()


# ---------- To'lov ----------
@router.callback_query(F.data.startswith("plan:"))
async def choose_plan(cb: CallbackQuery):
    plan = await get_plan(int(cb.data.split(":")[1]))
    if not plan:
        await cb.answer("Tarif topilmadi", show_alert=True)
        return
    sub = await get_or_create_subscriber(cb.from_user)
    await create_payment(sub, plan)
    desc = f"{escape(plan.description)}\n\n" if plan.description else "\n"
    await render(
        cb.message,
        f"{e('premium')} <b>{escape(plan.name)}</b> ({plan.duration_days} kun)\n{desc}"
        f"{e('card')} Kartaga <b>{plan.price_uzs:,} so'm</b> o'tkazing:\n\n"
        f"{card_block()}\n\n"
        "O'tkazgach <b>chek (screenshot)</b> rasmini shu yerga yuboring. "
        "Admin tekshirib, Premium ni faollashtiradi.",
        lambda: rows([kbtn("⬅️ Orqaga", "m:premium")]),
        edit=True)
    await cb.answer()


# ---------- AI Murabbiy ----------
BUSY = set()
AI_STEPS = ["goal", "level", "days", "place", "gender"]
AI_OPTIONS = {
    "goal": ("🎯 Maqsadingiz?", [("lose", "🔥 Ozish"), ("muscle", "💪 Massa"), ("shape", "🏃 Shakl")]),
    "level": ("📈 Tajriba darajangiz?", [("beginner", "Boshlang'ich"), ("mid", "O'rta"), ("pro", "Tajribali")]),
    "days": ("📅 Haftada necha kun mashq qila olasiz?", [(str(i), str(i)) for i in range(2, 7)]),
    "place": ("📍 Qayerda mashq qilasiz?", [("home", "🏠 Uyda"), ("gym", "🏢 Zalda")]),
    "gender": ("👤 Jinsingiz?", [("m", "👨 Erkak"), ("f", "👩 Ayol")]),
}


class Coach(StatesGroup):
    chat = State()
    profile = State()
    body = State()


def ai_step_kb(step):
    _, opts = AI_OPTIONS[step]
    return rows([kbtn(label, f"aip:{step}:{val}") for val, label in opts], [kbtn("⬅️ Orqaga", "m:ai")])


def ai_back_kb():
    return back_kb("m:ai", "⬅️ AI menyu")


async def show_ai_menu(msg: Message, sub, edit=True):
    used = await count_ai_today(sub)
    left = max(AI_LIMIT - used, 0)
    has_plan = bool((sub.profile or {}).get("last_plan"))
    text = (f"{e('ai')} <b>AI Murabbiy</b>\n\n"
            "Men sizga shaxsiy mashq va ovqatlanish rejasini tuzib beraman va savollaringizga javob beraman.\n\n"
            f"{e('clock')} Bugungi qolgan so'rovlar: <b>{left}/{AI_LIMIT}</b>")

    def kb():
        r = [[kbtn("📝 Shaxsiy reja tuzish", "ai:plan")], [kbtn("💬 Savol berish", "ai:chat")]]
        if has_plan:
            r.append([kbtn("📋 Oxirgi rejam", "ai:last")])
        r.append([kbtn("🧹 Suhbatni tozalash", "ai:clear")])
        r.append([kbtn("⬅️ Orqaga", "m:home")])
        return rows(*r)

    await render(msg, text, kb, edit=edit)


async def ai_allowed(message: Message, sub) -> bool:
    uid = message.from_user.id
    if not sub.is_premium:
        await message.answer(f"{e('lock')} AI Murabbiy faqat Premium uchun.")
        return False
    problem = ai.config_problem()
    if problem:
        await message.answer(ai_problem_text(problem, uid))
        return False
    if uid in BUSY:
        await message.answer(f"{e('clock')} Oldingi so'rovingiz hali bajarilmoqda, biroz kuting.")
        return False
    if await count_ai_today(sub) >= AI_LIMIT:
        await message.answer(f"{e('clock')} Bugungi limit tugadi ({AI_LIMIT} ta so'rov). Ertaga davom eting.")
        return False
    return True


def ai_problem_text(problem: str, user_id: int) -> str:
    if is_admin(user_id):
        return (f"🤖 AI sozlanmagan.\n🔧 Sabab (faqat adminga ko'rinadi): {escape(problem)}\n"
                "Tekshirish: /aitest")
    return "🤖 AI hozircha ishlamayapti. Birozdan keyin urinib ko'ring."


async def safe_generate(message: Message, messages, extra_system="", max_tokens=1500):
    try:
        return await ai.generate(messages, extra_system, max_tokens)
    except ai.AINotConfigured as ex:
        await message.answer(ai_problem_text(ex.reason, message.from_user.id))
    except ai.AIError as ex:
        text = "⚠️ AI hozir javob bera olmadi. Birozdan keyin qayta urinib ko'ring."
        if is_admin(message.from_user.id):
            text += (f"\n\n🔧 Sabab (faqat adminga ko'rinadi): {escape(ex.reason)}\n"
                     "Tekshirish: /aitest")
        await message.answer(text)
    return None


async def send_long(message: Message, text: str):
    chunks = ai.split_text(ai.to_html(text), 3800)
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        kb = ai_back_kb() if last else None
        try:
            await message.answer(chunk, reply_markup=kb)
        except TelegramBadRequest:  # HTML buzilsa, oddiy matn sifatida yuboramiz
            plain_text = unescape(re.sub(r"<[^>]+>", "", chunk))
            await message.answer(plain_text, parse_mode=None, reply_markup=kb)


@router.callback_query(F.data.startswith("ai:"))
async def ai_cb(cb: CallbackQuery, state: FSMContext):
    sub = await get_or_create_subscriber(cb.from_user)
    if not sub.is_premium:
        await cb.answer("Faqat Premium uchun", show_alert=True)
        return
    action = cb.data[3:]
    problem = ai.config_problem()
    if action in ("chat", "plan") and problem:
        await cb.answer(("AI sozlanmagan: " + problem) if is_admin(cb.from_user.id)
                        else "AI hozircha ishlamayapti. Keyinroq urinib ko'ring.", show_alert=True)
        return
    if action == "chat":
        await state.set_state(Coach.chat)
        await render(cb.message,
                     f"{e('ai')} <b>AI Murabbiy bilan suhbat</b>\n\n"
                     "Savolingizni yozing: mashq, ovqatlanish, tiklanish...\n"
                     "Masalan: <i>«Uyda yelka uchun mashqlar ber»</i>\n\n"
                     "Chiqish uchun ⬅️ tugmasini bosing.",
                     ai_back_kb, edit=True)
    elif action == "plan":
        await state.set_state(Coach.profile)
        await render(cb.message, f"{e('ai')} <b>Shaxsiy reja</b>\n\n{AI_OPTIONS['goal'][0]}",
                     lambda: ai_step_kb("goal"), edit=True)
    elif action == "last":
        last = (sub.profile or {}).get("last_plan")
        if not last:
            await cb.answer("Hali reja tuzilmagan", show_alert=True)
            return
        await send_long(cb.message, last)
    elif action == "clear":
        await archive_ai_history(sub)
        await cb.answer("Suhbat tozalandi ✅", show_alert=True)
        return
    await cb.answer()


@router.callback_query(Coach.profile, F.data.startswith("aip:"))
async def plan_step(cb: CallbackQuery, state: FSMContext):
    try:
        _, step, value = cb.data.split(":")
        assert value in dict(AI_OPTIONS[step][1])
    except (ValueError, KeyError, AssertionError):
        await cb.answer()
        return
    await state.update_data(**{step: value})
    nxt = AI_STEPS.index(step) + 1
    if nxt < len(AI_STEPS):
        nstep = AI_STEPS[nxt]
        await render(cb.message, AI_OPTIONS[nstep][0], lambda: ai_step_kb(nstep), edit=True)
    else:
        await state.set_state(Coach.body)
        await render(cb.message,
                     "📏 Yosh, bo'y (sm) va vazn (kg) ni yozing.\nMasalan: <code>25 175 70</code>",
                     ai_back_kb, edit=True)
    await cb.answer()


@router.callback_query(F.data.startswith("aip:"))
async def plan_step_expired(cb: CallbackQuery):
    await cb.answer("Sessiya tugagan. AI menyudan qaytadan boshlang.", show_alert=True)


@router.message(Coach.body, F.text, ~F.text.startswith("/"))
async def plan_body(message: Message, state: FSMContext):
    nums = parse_numbers(message.text, 3)
    if not nums or not (14 <= nums[0] <= 80 and 120 <= nums[1] <= 230 and 35 <= nums[2] <= 250):
        await message.answer("Noto'g'ri format. Masalan: <code>25 175 70</code> (yosh, bo'y sm, vazn kg)")
        return
    sub = await get_or_create_subscriber(message.from_user)
    if not await ai_allowed(message, sub):
        return
    data = await state.get_data()
    await state.clear()
    profile = {**(sub.profile or {}),
               "goal": data.get("goal"), "level": data.get("level"), "days": data.get("days"),
               "place": data.get("place"), "gender": data.get("gender"),
               "age": int(nums[0]), "height": int(nums[1]), "weight": nums[2]}
    await save_profile(sub, profile)
    uid = message.from_user.id
    BUSY.add(uid)
    status = await message.answer(f"{e('clock')} Sizga shaxsiy reja tuzilmoqda (10–40 soniya)...")
    try:
        text = await safe_generate(message, [{"role": "user", "content": ai.plan_prompt(profile)}],
                                   max_tokens=3000)
        if text:
            profile["last_plan"] = text
            await save_profile(sub, profile)
            await save_ai_pair(sub, "Menga shaxsiy reja tuzib bering. " + ai.profile_line(profile), text)
            await send_long(message, text)
        else:
            await message.answer(ai.fallback_plan(profile), reply_markup=ai_back_kb())
    finally:
        BUSY.discard(uid)
        try:
            await status.delete()
        except TelegramBadRequest:
            pass


@router.message(Coach.chat, F.text, ~F.text.startswith("/"))
async def coach_chat(message: Message):
    sub = await get_or_create_subscriber(message.from_user)
    if not await ai_allowed(message, sub):
        return
    uid = message.from_user.id
    BUSY.add(uid)
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
        user_text = message.text.strip()[:1000]
        history = await ai_history(sub)
        answer = await safe_generate(message, history + [{"role": "user", "content": user_text}],
                                     ai.profile_context(sub.profile))
        if answer:
            await save_ai_pair(sub, user_text, answer)
            await send_long(message, answer)
    finally:
        BUSY.discard(uid)


# ---------- Adminga yozish + AI taklif qilgan javob ----------
SUP_ADMIN = F.from_user.id.in_(settings.ADMIN_IDS)
SUP_DAILY_LIMIT = 10


class Support(StatesGroup):
    write = State()
    reply = State()


def support_content(m: Message) -> bool:
    if m.text:
        return not m.text.startswith("/")
    return bool(m.photo or m.document)


def support_kb(ticket_id, has_suggestion):
    r = []
    if has_suggestion:
        r.append([kbtn("✅ Taklifni yuborish", f"sup:send:{ticket_id}")])
    r.append([kbtn("✏️ O'zim yozaman", f"sup:reply:{ticket_id}"), kbtn("🗑 Yopish", f"sup:close:{ticket_id}")])
    return rows(*r)


def support_header(ticket, sub) -> str:
    uname = f" (@{escape(sub.username)})" if sub.username else ""
    prem = "💎 Premium" if sub.is_premium else "🆓 Oddiy"
    return (f"✉️ <b>Yangi xabar</b> #{ticket.id}\n"
            f"👤 {escape(sub.full_name or str(sub.telegram_id))}{uname} • {prem}\n"
            f"🆔 <code>{sub.telegram_id}</code>")


async def deliver_reply(bot: Bot, ticket, text: str) -> bool:
    kb = rows([kbtn("✉️ Javob yozish", "m:support_new")])
    try:
        await bot.send_message(ticket.subscriber.telegram_id,
                               f"{e('support')} <b>Admin javobi:</b>\n\n{escape(text)}", reply_markup=kb)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False


async def finish_admin_msg(cb: CallbackQuery, note: str):
    try:
        await cb.message.edit_text(cb.message.html_text + f"\n\n{note}", reply_markup=None)
    except TelegramBadRequest:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


@router.message(Support.write, support_content)
async def support_write(message: Message, state: FSMContext):
    sub = await get_or_create_subscriber(message.from_user)
    if await count_support_today(sub) >= SUP_DAILY_LIMIT:
        await message.answer(f"{e('clock')} Bugungi xabarlar limiti tugadi. Iltimos, ertaga yozing.")
        return
    text = (message.text or message.caption or "").strip()[:1500]
    kind = "text" if message.text else "photo" if message.photo else "file"
    ticket = await create_support(sub, text, kind)
    await state.clear()
    await render(message, f"{e('check')} Xabaringiz adminga yuborildi. Javob shu yerda keladi.",
                 lambda: back_kb("m:home", "⬅️ Menyu"))

    # 1) adminlarga foydalanuvchi xabari
    header = support_header(ticket, sub)
    for admin_id in settings.ADMIN_IDS:
        try:
            if kind == "text":
                await message.bot.send_message(admin_id, f"{header}\n\n{escape(text)}")
            else:
                cap = header + (f"\n\n{escape(text[:600])}" if text else "")
                await message.copy_to(admin_id, caption=cap)
        except Exception:
            logging.exception("Adminga xabar yuborib bo'lmadi: %s", admin_id)

    # 2) AI tayyorlagan javob qoralamasi (Suggested Message)
    suggestion = None
    if not ai.config_problem():
        try:
            ctx = await support_context(sub)
            suggestion = await ai.suggest_reply(text or "(foydalanuvchi rasm yoki fayl yubordi)", ctx)
        except ai.AIError:
            suggestion = None
    if suggestion:
        await save_suggestion(ticket, suggestion)
        body = (f"💡 <b>Tavsiya etilgan javob</b> (#{ticket.id})\n\n{escape(suggestion)}\n\n"
                "<i>Bu AI qoralamasi: yuborishdan oldin tekshiring.</i>")
    else:
        body = f"💡 AI taklifi mavjud emas. #{ticket.id} ga o'zingiz javob yozing."
    for admin_id in settings.ADMIN_IDS:
        try:
            await message.bot.send_message(admin_id, body, reply_markup=support_kb(ticket.id, bool(suggestion)))
        except Exception:
            logging.exception("Adminga taklif yuborib bo'lmadi: %s", admin_id)


@router.callback_query(F.data.startswith("sup:"), SUP_ADMIN)
async def support_cb(cb: CallbackQuery, state: FSMContext, bot: Bot):
    try:
        _, action, sid = cb.data.split(":")
        ticket = await get_support(int(sid))
    except ValueError:
        await cb.answer()
        return
    if not ticket:
        await cb.answer("Xabar topilmadi", show_alert=True)
        return
    if ticket.status != SupportMessage.Status.OPEN:
        await cb.answer("Bu xabarga allaqachon javob berilgan yoki yopilgan", show_alert=True)
        return
    if action == "send":
        if not ticket.suggested:
            await cb.answer("Taklif yo'q", show_alert=True)
            return
        ok = await deliver_reply(bot, ticket, ticket.suggested)
        if ok:
            await mark_support(ticket, SupportMessage.Status.ANSWERED, ticket.suggested)
            await finish_admin_msg(cb, "✅ <b>Yuborildi</b>")
            await cb.answer("Yuborildi")
        else:
            await cb.answer("Yuborib bo'lmadi (foydalanuvchi botni bloklagan bo'lishi mumkin)", show_alert=True)
    elif action == "reply":
        await state.set_state(Support.reply)
        await state.update_data(ticket=ticket.id)
        await cb.message.answer(f"✏️ #{ticket.id} uchun javobingizni yozing.\nBekor qilish: /menu")
        await cb.answer()
    elif action == "close":
        await mark_support(ticket, SupportMessage.Status.CLOSED)
        await finish_admin_msg(cb, "🗑 <b>Yopildi</b>")
        await cb.answer("Yopildi")
    else:
        await cb.answer()


@router.message(Support.reply, SUP_ADMIN, F.text, ~F.text.startswith("/"))
async def support_admin_reply(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    ticket = await get_support(data.get("ticket"))
    await state.clear()
    if not ticket:
        await message.answer("Xabar topilmadi.")
        return
    text = message.text.strip()[:3000]
    if await deliver_reply(bot, ticket, text):
        await mark_support(ticket, SupportMessage.Status.ANSWERED, text)
        await message.answer(f"{e('check')} #{ticket.id}: javob foydalanuvchiga yuborildi.")
    else:
        await message.answer("⚠️ Yuborib bo'lmadi (foydalanuvchi botni bloklagan bo'lishi mumkin).")


@router.message(Command("aitest"), SUP_ADMIN)
async def ai_test(message: Message):
    problem = ai.config_problem()
    if problem:
        await message.answer(f"❌ AI sozlanmagan: {escape(problem)}")
        return
    key = ai.active_key()
    warn = f"\n⚠️ {escape(ai.key_warning())}" if ai.key_warning() else ""
    ws = ""
    if ai.provider() == "anthropic":
        ws = f"🗂 Workspace ID: {escape(ai.workspace_id() or 'kiritilmagan')}\n"
    await message.answer(f"🤖 Provayder: {escape(ai.provider_name())}\n"
                         f"🔑 Kalit: <code>{escape(key[:7])}...{escape(key[-4:])}</code>{warn}\n{ws}"
                         f"🧠 Modellar: {escape(', '.join(ai.active_models()))}\n⏳ Sinab ko'ryapman...")
    if ai.provider() == "groq":
        try:
            found = await ai.list_groq_models()
            await message.answer(f"📋 Groq hisobidagi modellar: {escape(', '.join(found[:8]) or 'ro`yxat bo`sh')}")
        except ai.AIError as ex:
            await message.answer(f"📋 Modellar ro'yxatini olib bo'lmadi: {escape(ex.reason)}")
    try:
        text = await ai.generate([{"role": "user", "content": "Faqat OK deb javob ber."}], max_tokens=20)
        await message.answer(f"✅ AI ishlayapti. Javob: {escape(text[:100])}")
    except ai.AIError as ex:
        await message.answer(f"❌ AI xatosi:\n{escape(ex.reason)}")


@router.message(F.photo | F.document)
async def receipt(message: Message):
    payment = await last_pending_card_payment(message.from_user.id)
    if not payment:
        if await has_review_payment(message.from_user.id):
            await message.answer(f"{e('clock')} Chekingiz allaqachon adminda tekshiruvda. Iltimos, kuting.")
        else:
            await message.answer("Avval /start → Premium sotib olish orqali tarifni tanlang.")
        return
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    await attach_receipt(payment, file_id)
    await render(message, f"{e('clock')} Chek qabul qilindi. Admin tekshirgach Premium faollashadi.",
                 lambda: back_kb("m:home", "⬅️ Menyu"))
    for admin_id in settings.ADMIN_IDS:
        try:
            await message.copy_to(admin_id, caption=receipt_caption(payment), reply_markup=review_kb(payment.id))
        except Exception:
            logging.exception("Adminga chek yuborib bo'lmadi: %s", admin_id)


# ---------- Admin panel ----------
ADMIN = F.from_user.id.in_(settings.ADMIN_IDS)


def admin_home_kb(n):
    return rows([kbtn(f"🧾 Kutilayotgan cheklar ({n})", "adm:pending")],
                [kbtn("📊 Statistika", "adm:stats")],
                [kbtn("⬅️ Menyu", "m:home")])


async def admin_panel(target: Message, edit=False):
    st = await admin_stats()
    text = (f"🛠 <b>Admin panel</b>\n\n"
            f"👥 Foydalanuvchilar: <b>{st['users']}</b>\n"
            f"{e('premium')} Premium: <b>{st['premium']}</b>\n"
            f"🧾 Kutilayotgan cheklar: <b>{st['review']}</b>\n\n{card_block()}")
    await render(target, text, lambda: admin_home_kb(st["review"]), edit=edit)


@router.message(Command("admin"), ADMIN)
async def admin_cmd(message: Message, state: FSMContext):
    await state.clear()
    await admin_panel(message)


@router.callback_query(F.data == "adm:home", ADMIN)
async def admin_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await admin_panel(cb.message, edit=True)
    await cb.answer()


@router.callback_query(F.data == "adm:stats", ADMIN)
async def admin_stats_cb(cb: CallbackQuery):
    st = await admin_stats()
    income = "\n".join(f"• {v:,.0f} {c}" if c == "UZS" else f"• {v:.2f} {c}" for c, v in st["income"].items())
    text = (f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: {st['users']}\n"
            f"{e('premium')} Faol Premium: {st['premium']}\n"
            f"{e('check')} To'langan to'lovlar: {st['paid']}\n"
            f"🧾 Kutilayotgan cheklar: {st['review']}\n\n"
            f"💰 <b>Tushum</b>\n{income or 'Hali yo`q'}")
    await render(cb.message, text, lambda: back_kb("adm:home"), edit=True)
    await cb.answer()


@router.callback_query(F.data == "adm:pending", ADMIN)
async def admin_pending(cb: CallbackQuery):
    pays = await list_review_payments()
    if not pays:
        await render(cb.message, f"{e('check')} Kutilayotgan cheklar yo'q.", lambda: back_kb("adm:home"), edit=True)
    else:
        await render(
            cb.message, "🧾 <b>Kutilayotgan cheklar</b>\nKo'rish uchun tanlang:",
            lambda: rows(*[[kbtn(f"#{p.id} {p.subscriber.full_name or p.subscriber.telegram_id} • {fmt_amount(p)}",
                                 f"adm:view:{p.id}")] for p in pays], [kbtn("⬅️ Orqaga", "adm:home")]),
            edit=True)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:view:"), ADMIN)
async def admin_view(cb: CallbackQuery, bot: Bot):
    payment = await get_payment_admin(int(cb.data.split(":")[2]))
    if not payment or payment.status != Payment.Status.REVIEW or not payment.receipt_file_id:
        await cb.answer("Bu chek allaqachon ko'rib chiqilgan", show_alert=True)
        return
    cap, kb = receipt_caption(payment), review_kb(payment.id)
    try:
        await bot.send_photo(cb.from_user.id, payment.receipt_file_id, caption=cap, reply_markup=kb)
    except TelegramBadRequest:
        await bot.send_document(cb.from_user.id, payment.receipt_file_id, caption=cap, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.func(lambda d: d.startswith(("adm:ok:", "adm:no:"))), ADMIN)
async def admin_decide(cb: CallbackQuery):
    _, action, pid = cb.data.split(":")
    payment = await get_payment_admin(int(pid))
    if not payment:
        await cb.answer("To'lov topilmadi", show_alert=True)
        return
    if payment.status in (Payment.Status.PAID, Payment.Status.REJECTED):
        await cb.answer("Bu to'lov allaqachon ko'rib chiqilgan", show_alert=True)
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        return
    if action == "ok":
        await sync_to_async(activate_payment)(payment)
        verdict = f"✅ <b>Tasdiqlandi</b> ({escape(cb.from_user.full_name)})"
    else:
        await sync_to_async(reject_payment)(payment)
        verdict = f"❌ <b>Rad etildi</b> ({escape(cb.from_user.full_name)})"
    try:
        await cb.message.edit_caption(caption=receipt_caption(payment) + "\n\n" + verdict, reply_markup=None)
    except TelegramBadRequest:
        pass
    await cb.answer("Bajarildi")


async def run_bot():
    logging.basicConfig(level=logging.INFO)
    token = (settings.BOT_TOKEN or "").strip().strip("\"'")
    if not token or ":" not in token:
        print("\n❌ BOT_TOKEN yo'q yoki noto'g'ri formatda.\n"
              "   .env fayliga BotFather bergan tokenni yozing: BOT_TOKEN=123456789:AAH... (bo'sh joy va qo'shtirnoqsiz).\n"
              "   .env fayli manage.py bilan bir papkada bo'lishi kerak.")
        return
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        print(f"\n❌ Telegram tokenni tanimadi (Unauthorized). .env dagi token ID si: {token.split(':')[0]}\n"
              "   Sabab: token noto'g'ri, eskirgan yoki /revoke qilingan.\n"
              "   Yechim: Telegram'da @BotFather -> /mybots -> botni tanlang -> API Token -> tokenni nusxalab\n"
              "   .env dagi BOT_TOKEN= qatoriga yozing va botni qayta ishga tushiring.")
        await bot.session.close()
        return
    except TelegramNetworkError as ex:
        print(f"\n❌ Telegram serveriga ulanib bo'lmadi: {ex}\n   Internetni tekshiring yoki VPN yoqib ko'ring.")
        await bot.session.close()
        return
    print(f"✅ Bot ulandi: @{me.username}")
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)
