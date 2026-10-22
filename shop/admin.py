from django.contrib import admin, messages
from .models import Plan, Subscriber, Payment, AIMessage, SupportMessage
from .services.subscriptions import activate_payment, reject_payment


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price_uzs", "duration_days", "is_active")


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ("full_name", "username", "telegram_id", "premium_until")
    search_fields = ("full_name", "username", "telegram_id")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "subscriber", "plan", "method", "amount", "currency", "status", "created_at")
    list_filter = ("status", "method")
    actions = ["approve", "reject"]

    @admin.action(description="Tanlangan to'lovlarni TASDIQLASH")
    def approve(self, request, queryset):
        n = sum(activate_payment(p) for p in queryset)
        self.message_user(request, f"{n} ta to'lov tasdiqlandi", messages.SUCCESS)

    @admin.action(description="Tanlangan to'lovlarni RAD ETISH")
    def reject(self, request, queryset):
        for p in queryset:
            reject_payment(p)
        self.message_user(request, "Rad etildi", messages.WARNING)


@admin.register(AIMessage)
class AIMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "subscriber", "role", "created_at", "archived")
    list_filter = ("role", "archived")


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "subscriber", "status", "created_at")
    list_filter = ("status",)
