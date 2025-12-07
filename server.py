import os
import asyncio
from aiohttp import web
from dotenv import load_dotenv

from discord_bot import bot  # importe ton bot déjà configuré

load_dotenv()  # utile en local, sur Render ce seront les env vars

# ---------- AIOHTTP WEB APP (pour Render / BetterStack) ----------

async def handle_root(request):
    return web.Response(text="InfinityStream Discord bot is running ✅")

def create_web_app():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_root)
    return app

async def main():
    # 1) Lancer le serveur web
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))  # Render fournit PORT
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[WEB] Server started on port {port}")

    # 2) Lancer le bot Discord dans la même boucle
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("La variable d'environnement DISCORD_TOKEN est manquante.")

    print("[DISCORD] Starting bot...")
    await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down...")
