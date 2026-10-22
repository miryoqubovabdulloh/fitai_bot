from datetime import timedelta
from django.utils import timezone
from shop.models import Payment
from .notify import send_message


def activate_payment(payment: Payment) -> bool:
    """To'lovni tasdiqlaydi va obunani uzaytiradi. Ikki marta ishlamaydi."""
    if payment.status == Payment.Status.PAID:
        return False
    now = timezone.now()
    sub = payment.subscriber
    base = sub.premium_until if sub.premium_until and sub.premium_until > now else now
    sub.premium_until = base + timedelta(days=payment.plan.duration_days)
    sub.save(update_fields=["premium_until"])
    payment.status = Payment.Status.PAID
    payment.paid_at = now
    payment.save(update_fields=["status", "paid_at"])
    send_message(
        sub.telegram_id,
        f"✅ To'lov tasdiqlandi! FitAI Premium {sub.premium_until:%d.%m.%Y} gacha faol.",
    )
    return True


def reject_payment(payment: Payment) -> None:
    payment.status = Payment.Status.REJECTED
    payment.save(update_fields=["status"])
    send_message(payment.subscriber.telegram_id, "❌ To'lovingiz rad etildi. Savol bo'lsa adminga yozing.")
