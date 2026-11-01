"""Load the app's gettext catalogs and persist its language choice."""

import gettext
import json
import os
from pathlib import Path

DOMAIN = "kiro-iso-builder"
PREFS_PATH = os.path.expanduser("~/.config/kiro-iso-builder/prefs.json")
_LOCAL_LOCALE_DIR = Path(__file__).resolve().parent / "locale"
LOCALE_DIR = _LOCAL_LOCALE_DIR if _LOCAL_LOCALE_DIR.is_dir() else Path("/usr/share/locale")

NAMES = {
    "en": "English", "nl": "Nederlands", "de": "Deutsch", "es": "Español", "fr": "Français",
    "pt_BR": "Português (Brasil)", "ru": "Русский", "hu": "Magyar", "it": "Italiano", "tr": "Türkçe",
    "pl": "Polski", "ro": "Română", "zh_CN": "简体中文", "ja": "日本語",
}


def available():
    """Return English and every language with a compiled catalog."""
    return ["en", *sorted(p.parent.parent.name for p in LOCALE_DIR.glob(f"*/LC_MESSAGES/{DOMAIN}.mo"))]


def name(code):
    """Return the language's own name, or its code when unknown."""
    return NAMES.get(code) or NAMES.get(code.split("_")[0]) or code


def _load_prefs():
    try:
        with open(PREFS_PATH, encoding="utf-8") as prefs_file:
            prefs = json.load(prefs_file)
    except (OSError, ValueError):
        return {}
    return prefs if isinstance(prefs, dict) else {}


def chosen():
    """Return the saved language choice, or None to follow the system."""
    code = _load_prefs().get("language")
    return code if code in available() else None


def save_choice(code):
    """Save a language choice; None means follow the system."""
    prefs = _load_prefs()
    prefs["language"] = code
    os.makedirs(os.path.dirname(PREFS_PATH), exist_ok=True)
    with open(PREFS_PATH, "w", encoding="utf-8") as prefs_file:
        json.dump(prefs, prefs_file, indent=2)


def _system_language():
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "").split(":")[0].split(".")[0]
        if value:
            return value
    return "en"


_SIBLING = {"pt": "pt_BR", "zh": "zh_CN"}


def _shown(code):
    codes = available()
    base = code.split("_")[0]
    for candidate in (code, base, _SIBLING.get(base)):
        if candidate in codes:
            return candidate
    return "en"


SYSTEM = _shown(_system_language())
ACTIVE = os.environ.get("KIB_LANGUAGE") or chosen()
_SYSTEM_LANGUAGE_VAR = os.environ.get("LANGUAGE")
if ACTIVE:
    os.environ["LANGUAGE"] = ACTIVE


def restore_environment():
    """Restore LANGUAGE before re-executing so System uses the session locale."""
    if _SYSTEM_LANGUAGE_VAR is None:
        os.environ.pop("LANGUAGE", None)
    else:
        os.environ["LANGUAGE"] = _SYSTEM_LANGUAGE_VAR


_translation = gettext.translation(DOMAIN, localedir=LOCALE_DIR, languages=[ACTIVE or SYSTEM], fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext


def N_(text):
    """Mark a string for translation without translating it here."""
    return text
