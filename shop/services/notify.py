import requests
from django.conf import settings


def send_message(chat_id: int, text: str) -> None:
    if not settings.BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except requests.RequestException:
        pass
