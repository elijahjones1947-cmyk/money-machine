web: gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 30
worker: python discord_bot.py
