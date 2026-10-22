# FitAI Bot (Django + aiogram)

## Ishga tushirish
```
pip install -U -r requirements.txt
copy .env.example .env      (BOT_TOKEN, ADMIN_IDS, GROQ_API_KEY ni to'ldiring)
py manage.py runbot
```
`runbot` bazani o'zi tekshiradi va tuzatadi. Telegram'da admin akkauntdan `/aitest` bilan AI ni sinang.

## AI (bepul): Groq
1. https://console.groq.com/keys dan kalit oling
2. `.env` ga: `GROQ_API_KEY=gsk_...`
Claude API (pullik) ixtiyoriy: `ANTHROPIC_API_KEY`. `AI_PROVIDER=auto`: Groq kaliti bo'lsa Groq ishlatiladi.

## Qanday ishlaydi
- Tariflar: 1 oy = 100 000, 3 oy = 500 000 so'm. To'lov: karta + chek, admin Telegram ichida tasdiqlaydi (`/admin`).
- AI Murabbiy (Premium): shaxsiy reja va savol-javob, kunlik limit `AI_DAILY_LIMIT`.
- Adminga yozish: AI javob qoralamasi bilan.
- Premium emoji: admin akkauntdan `premium` + premium emoji yuboring (`/emojilar`).
