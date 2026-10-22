"""
Emoji sozlamasi (oddiy va premium/custom emoji).

Premium emoji ID larini kodga yozish shart emas:
admin akkauntingizdan botga shunday xabar yuboring:

    premium 💎      (💎 o'rniga haqiqiy premium emoji)
    fire 🔥
    muscle 💪

Bot har bir so'zdan keyingi premium emojining ID sini emoji_ids.json
fayliga saqlaydi va darhol ishlata boshlaydi.
Mavjud nomlarni ko'rish: /emojilar
"""
import json
from pathlib import Path

# nom: (oddiy emoji, standart premium emoji ID — odatda bo'sh)
EMOJIS = {
    "premium": ("💎", ""),
    "crown": ("👑", ""),
    "gold": ("🥇", ""),
    "bronze": ("🥉", ""),
    "lock": ("🔒", ""),
    "check": ("✅", ""),
    "cross": ("❌", ""),
    "muscle": ("💪", ""),
    "workout": ("🏋️", ""),
    "fire": ("🔥", ""),
    "food": ("🥗", ""),
    "profile": ("👤", ""),
    "bmi": ("🧮", ""),
    "card": ("💳", ""),
    "star": ("⭐", ""),
    "clock": ("⏳", ""),
    "free": ("🆓", ""),
    "info": ("ℹ️", ""),
    "history": ("🕘", ""),
    "ai": ("🤖", ""),
    "support": ("✉️", ""),
    "water": ("💧", ""),
    "sleep": ("😴", ""),
}

_FILE = Path(__file__).resolve().parent.parent / "emoji_ids.json"


def _load() -> dict:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_IDS = _load()


def emoji_id(name: str) -> str:
    return _IDS.get(name) or EMOJIS[name][1]


def set_emoji_id(name: str, cid: str) -> None:
    _IDS[name] = cid
    _FILE.write_text(json.dumps(_IDS, ensure_ascii=False, indent=2), encoding="utf-8")


def e(name: str) -> str:
    """Matn (HTML) ichida: premium emoji bo'lsa shuni, bo'lmasa oddiy emojini beradi."""
    char = EMOJIS[name][0]
    cid = emoji_id(name)
    if cid:
        return f'<tg-emoji emoji-id="{cid}">{char}</tg-emoji>'
    return char


def plain(name: str) -> str:
    return EMOJIS[name][0]
