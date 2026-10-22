from django.db import models
from django.utils import timezone


class Plan(models.Model):
    name = models.CharField("Nomi", max_length=100)
    description = models.TextField("Tavsif", blank=True)
    price_uzs = models.PositiveIntegerField("Narx (so'm)")
    duration_days = models.PositiveIntegerField("Muddat (kun)", default=30)
    is_active = models.BooleanField("Faol", default=True)

    def __str__(self):
        return f"{self.name} ({self.duration_days} kun)"


class Subscriber(models.Model):
    telegram_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=100, blank=True)
    full_name = models.CharField(max_length=200, blank=True)
    premium_until = models.DateTimeField(null=True, blank=True)
    profile = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_premium(self):
        return bool(self.premium_until and self.premium_until > timezone.now())

    def __str__(self):
        return self.full_name or str(self.telegram_id)


class Payment(models.Model):
    class Method(models.TextChoices):
        CARD = "card", "Karta"

    class Status(models.TextChoices):
        PENDING = "pending", "Kutilmoqda"
        REVIEW = "review", "Tekshiruvda"
        PAID = "paid", "To'langan"
        REJECTED = "rejected", "Rad etilgan"

    subscriber = models.ForeignKey(Subscriber, on_delete=models.CASCADE, related_name="payments")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.CARD)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=10)
    receipt_file_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"#{self.pk} {self.subscriber} {self.amount} {self.currency}"


class AIMessage(models.Model):
    subscriber = models.ForeignKey(Subscriber, on_delete=models.CASCADE, related_name="ai_messages")
    role = models.CharField(max_length=10)  # user / assistant
    content = models.TextField()
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"


class SupportMessage(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Ochiq"
        ANSWERED = "answered", "Javob berilgan"
        CLOSED = "closed", "Yopilgan"

    subscriber = models.ForeignKey(Subscriber, on_delete=models.CASCADE, related_name="support_messages")
    text = models.TextField(blank=True)
    kind = models.CharField(max_length=10, default="text")
    suggested = models.TextField(blank=True)
    answer = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"#{self.pk} {self.subscriber}: {self.text[:40]}"
