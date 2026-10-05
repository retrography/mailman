"""Writing systems. Fixed knowledge, not configuration: Unicode ranges and the letters that tell the
languages of the Arabic script apart."""

from __future__ import annotations

import re

_RANGES = {
    "Latin": "A-Za-zÀ-ɏḀ-ỿ",
    "Greek": "Ͱ-Ͽἀ-῿",
    "Cyrillic": "Ѐ-ԯ",
    "Armenian": "԰-֏",
    "Hebrew": "֐-׿",
    "Arabic": "؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿",
    "Devanagari": "ऀ-ॿ",
    "Bengali": "ঀ-৿",
    "Gurmukhi": "਀-੿",
    "Gujarati": "઀-૿",
    "Tamil": "஀-௿",
    "Telugu": "ఀ-౿",
    "Kannada": "ಀ-೿",
    "Malayalam": "ഀ-ൿ",
    "Sinhala": "඀-෿",
    "Thai": "฀-๿",
    "Lao": "຀-໿",
    "Myanmar": "က-႟",
    "Georgian": "Ⴀ-ჿ",
    "Ethiopic": "ሀ-፿",
    "Khmer": "ក-៿",
    "Korean": "ᄀ-ᇿ㄰-㆏가-힯",       # Hangul
    "Kana": "぀-ヿㇰ-ㇿｦ-ﾟ",         # hiragana and katakana: only Japanese writes them
    "Han": "㐀-䶿一-鿿豈-﫿",          # Chinese characters, shared by Chinese and Japanese
}
_RX = {name: re.compile(f"[{chars}]") for name, chars in _RANGES.items()}
SCRIPTS = [n for n in _RANGES if n not in ("Kana", "Han")] + ["Japanese", "Chinese"]   # the values `script` reports

# One of these letters settles the language, in this order; Arabic has none of the Persian four,
# Urdu and Pashto do and are checked first.
_DECISIVE = {"ur": "ٹڈڑںےہھ", "ps": "ټډړښږڼېۍ", "fa": "پچژگ"}
# Otherwise the language with more of its own letters. Persian typed on an Arabic keyboard uses the
# Arabic ي and ك, hence only a count.
_MOST = {"fa": "کی", "ar": "ةكيى"}


def script(text: str) -> str:
    """The writing system most of the text's letters are in, or "none". Chinese characters together
    with kana are Japanese; without kana, Chinese (simplified and traditional are not told apart)."""
    counts = {name: len(rx.findall(text)) for name, rx in _RX.items()}
    kana, han = counts.pop("Kana"), counts.pop("Han")
    counts["Japanese" if kana else "Chinese"] = kana + han
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] else "none"


def arabic_script_language(text: str) -> str | None:
    """Which Arabic-script language a text is in, from the letters only that language writes. None when the
    text is not mainly in Arabic script or no such letter occurs: the language detector decides."""
    letters = [c for c in text if c.isalpha()]
    if 2 * len(_RX["Arabic"].findall(text)) <= len(letters):
        return None
    for lang, own in _DECISIVE.items():
        if any(c in own for c in letters):
            return lang
    counts = {lang: sum(c in own for c in letters) for lang, own in _MOST.items()}
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] and list(counts.values()).count(counts[best]) == 1 else None


# Writing systems that (for mail) mean one language. Latin and Cyrillic are shared by many: no answer.
_ONE_LANGUAGE = {"Hebrew": "he", "Devanagari": "hi", "Bengali": "bn", "Tamil": "ta", "Thai": "th", "Korean": "ko",
                 "Japanese": "ja", "Chinese": "zh", "Greek": "el"}


def language(text: str) -> str | None:
    """The language of a text as far as its writing system tells it (ISO 639-1), else None."""
    kind = script(text)
    return arabic_script_language(text) if kind == "Arabic" else _ONE_LANGUAGE.get(kind)
