# 🤖 Trading Bot — Railway Deploy Guide

## What this is
Your paper trading bot + a live web dashboard, running 24/7 on Railway (free tier).
Visit your Railway URL to see balance, open positions, and every trade in real time.

---

## Step 1 — Get your Binance API key (read-only)

1. Go to [binance.com](https://www.binance.com) → Account → API Management
2. Create a new API key
3. **Enable: "Read Info" only** — disable trading, disable withdrawals
   (this is paper trading — the bot never places real orders)
4. Copy your **API Key** and **Secret Key** — you'll need them in Step 4

---

## Step 2 — Push this code to GitHub

```bash
cd trading-bot
git init
git add .
git commit -m "trading bot"
# Create a new repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/trading-bot.git
git push -u origin main
```

---

## Step 3 — Deploy to Railway

1. Go to [railway.app](https://railway.app) → sign up (free)
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `trading-bot` repo
4. Railway will auto-detect Python and deploy

---

## Step 4 — Set your API keys on Railway

1. In your Railway project, click your service → **Variables** tab
2. Add these two variables:
   ```
   BINANCE_API_KEY   =  paste_your_key_here
   BINANCE_SECRET    =  paste_your_secret_here
   ```
3. Railway will automatically restart the bot with the keys

---

## Step 5 — Visit your dashboard

1. Click **Settings** → **Domains** → **Generate Domain**
2. Open the URL — you'll see the live dashboard
3. Dashboard auto-refreshes every 30 seconds

---

## Dashboard pages

| URL | What it shows |
|-----|--------------|
| `/` | Live dashboard — balance, positions, trade log |
| `/api/state` | Raw JSON (for debugging) |
| `/health` | Bot running status |

---

## Notes

- **Paper trading only** — no real money is spent, ever
- Bot runs every 60 seconds (1 cycle per minute)
- Free Railway tier gives 500 hours/month — enough for ~20 days continuous
- Upgrade to Railway Hobby ($5/mo) for unlimited runtime

---

## Files

```
app.py           ← Flask dashboard + bot thread (main file)
requirements.txt ← Python dependencies
Procfile         ← Tells Railway how to start the app
railway.toml     ← Railway config
.env.example     ← Copy to .env for local testing
```
