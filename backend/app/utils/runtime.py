"""Commands that work with Python and with the portable server executable."""
import os
import sys


def ytdlp_command() -> list[str]:
    # In a PyInstaller application sys.executable is server.exe, not Python.
    # Its private dispatch mode retains the subprocess deadlines used by lookups.
    command = ([sys.executable, "--yt-dlp"] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "yt_dlp"])
    runtime = os.environ.get("YTDLP_JS_RUNTIME", "").strip()
    runtimes = [runtime] if runtime else ["node", "deno"]
    for value in runtimes:
        command.extend(["--js-runtimes", value])
    return command
