import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def p(msg):
    print(msg, flush=True)


def main():
    p("=== Discord PrintBot Doctor ===")
    p(f"Python executable: {sys.executable}")
    p(f"Python version: {sys.version.split()[0]}")
    p(f"Current working directory: {Path.cwd()}")

    this_file = Path(__file__).resolve()
    p(f"Doctor file: {this_file}")

    bot_file = (Path.cwd() / "bot.py").resolve()
    p(f"Expected bot.py: {bot_file}")
    p(f"bot.py exists: {bot_file.exists()}")

    if bot_file.exists():
        with bot_file.open("r", encoding="utf-8", errors="replace") as f:
            head = [next(f, "").rstrip("\n") for _ in range(12)]
        p("--- First lines of bot.py ---")
        for i, line in enumerate(head, start=1):
            p(f"{i:02d}: {line}")

        header_blob = "\n".join(head).lower()
        if "epson thermal printer manager" in header_blob or "escpos" in header_blob:
            p("WARNING: bot.py appears to contain printer code, not Discord bot code.")
            p("Fix: restore bot.py from this repo version before running.")

    env_file = (Path.cwd() / ".env").resolve()
    p(f".env exists: {env_file.exists()} ({env_file})")
    load_dotenv(dotenv_path=env_file)

    token = (os.getenv("DISCORD_TOKEN") or "").strip()
    p(f"DISCORD_TOKEN present: {bool(token)}")
    if token:
        p(f"DISCORD_TOKEN length: {len(token)}")
        p(f"DISCORD_TOKEN placeholder: {token.lower() == 'your_bot_token_here'}")

    try:
        import discord  # noqa: F401
        p("discord import: OK")
    except Exception as e:
        p(f"discord import: FAIL -> {e}")

    try:
        from printer import PrinterManager
        p("printer import: OK")
        pm = PrinterManager()
        p("PrinterManager init: OK")
        try:
            pm.check_printer_connection()
            p("USB printer connection check: OK")
        except Exception as e:
            p(f"USB printer connection check: FAIL -> {e}")
    except Exception as e:
        p(f"printer import/init: FAIL -> {e}")

    p("=== Doctor complete ===")


if __name__ == "__main__":
    main()
