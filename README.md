# Universal Video Downloader Bot

## O'rnatish (Termux / VPS / kompyuter)

```bash
pip install -r requirements.txt
# yoki Termux'da FFmpeg ham kerak:
pkg install ffmpeg -y
```

## Token qo'shish (MUHIM — xavfsiz usul)

1. `.env.example` faylini nusxalab, nomini `.env` ga o'zgartiring:
   ```bash
   cp .env.example .env
   ```
2. `.env` faylni oching va haqiqiy qiymatlarni kiriting:
   ```
   BOT_TOKEN=123456:ABC-sizning-haqiqiy-tokeningiz
   ADMIN_IDS=sizning_telegram_id_raqamingiz
   ```
   Tokenni @BotFather'dan olasiz. Telegram ID raqamingizni @userinfobot orqali bilib olasiz.

3. Botni ishga tushiring:
   ```bash
   python bot.py
   ```

`.env` fayl `.gitignore` ichida — shuning uchun GitHub'ga hech qachon yuklanmaydi va token oshkor bo'lib qolmaydi.

## GitHub'ga yuklash

```bash
git init
git add .
git commit -m "Video downloader bot"
git branch -M main
git remote add origin https://github.com/USERNAME/REPO-NAME.git
git push -u origin main
```

`git add .` buyrug'i `.env` faylni **qo'shmaydi**, chunki u `.gitignore` da ko'rsatilgan — faqat `bot.py`, `requirements.txt`, `.env.example` va `.gitignore` yuklanadi.

## Eslatma

- Agar `.env` ishlatmoqchi bo'lmasangiz, muhit o'zgaruvchisi sifatida ham berishingiz mumkin:
  ```bash
  export BOT_TOKEN="123456:ABC-token"
  export ADMIN_IDS="123456789"
  python bot.py
  ```
- Repo'ni **Private** qilib qo'yish tavsiya etiladi, ayniqsa boshida hali ehtiyot choralarga to'liq amal qilmagan bo'lsangiz.
