"""Persist the last-used form values so the next launch starts where you
left off. Storage is a plain JSON file next to the package (portable app),
and every failure is non-fatal — settings are a convenience, never a
requirement."""
import json
import os

_FILENAME = "tvm_settings.json"


def settings_path():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, _FILENAME)


def load_settings():
    """Return the saved settings dict; {} when missing or unreadable."""
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    """Write the settings dict; returns True on success."""
    tmp = settings_path() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, settings_path())
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
