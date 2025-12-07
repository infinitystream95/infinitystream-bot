import os
import asyncio
from aiohttp import web
from dotenv import load_dotenv

from discord_bot import bot
from telegram_bot import build_telegram_app

load_dotenv()


# ---------- HTTP (Render / BetterStack) ----------

async def handle_root(request):
    return web.Response(text="InfinityStream multi-bot is running ✅")

async def handle_health(request):
    return web.Response(text="OK")


def create_web_app():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    return app


# ---------- MAIN ----------

async def main():
    # --- HTTP pour Render & BetterStack ---
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[WEB] Server démarré sur le port {port}")

    # --- Telegram ---
    telegram_app = build_telegram_app()
    await telegram_app.initialize()
    await telegram_app.start()
    # lance le polling en tâche de fond
    await telegram_app.updater.start_polling()
    print("[TELEGRAM] Bot Telegram démarré (polling)")

    # --- Discord ---
    discord_token = os.getenv("DISCORD_TOKEN")
    if not discord_token:
        raise RuntimeError("La variable d'environnement DISCORD_TOKEN est manquante.")

    print("[DISCORD] Démarrage du bot Discord…")
    try:
        # Cette ligne bloque tant que le bot Discord est en ligne,
        # mais laisse tourner Telegram + HTTP dans la même boucle asyncio.
        await bot.start(discord_token)
    finally:
        # Arrêt propre de Telegram si le process se termine
        print("[TELEGRAM] Arrêt du bot Telegram…")
        try:
            await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception as e:
            print(f"[TELEGRAM] Erreur à l'arrêt : {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Arrêt manuel (Ctrl+C)")
