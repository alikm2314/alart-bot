"""Alart application."""
import logging

# httpx logs request URLs at INFO; Telegram Bot API URLs include the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
