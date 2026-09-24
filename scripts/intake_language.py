#!/usr/bin/env python3
"""Which language the intake pages speak -- decided once, here, for every script that asks.

The profile collects `identity_and_language.preferred_response_language`, and until 2026-09-24
nothing downstream read it: the trip form received it and dropped it, 0 of 15 real intakes carried
it, and every page was Chinese. The order of sources is the spec's (decision 2): an explicit
`--language`, else the saved profile's preferred output language, else Chinese.

The profile stores the traveller's own words for that preference ("中文", "English",
"中文和 English", "其他"), so they are normalised here rather than compared anywhere else. A
bilingual or unrecognised answer names no single page language and resolves to the next source.
"""

from __future__ import annotations

LANGUAGES = ("zh", "en")
# Both forms send their CURRENT language with every submission (it follows the page's switch), so
# a server answers in the language on screen and the intake records the one actually used.
LANGUAGE_HEADER = "X-Travel-Buddy-Language"

_ALIASES = {
    "en": "en", "english": "en", "英文": "en", "英语": "en", "en-us": "en", "en-gb": "en",
    "zh": "zh", "chinese": "zh", "中文": "zh", "汉语": "zh", "简体中文": "zh", "zh-cn": "zh",
    "zh-hans": "zh",
}


def normalize_language(value: object) -> str | None:
    """"English"/"en"/"英文" -> "en"; "中文"/"zh"/"Chinese" -> "zh"; anything else -> None."""
    if not isinstance(value, str):
        return None
    return _ALIASES.get(value.strip().lower())


def resolve_form_language(cli: str | None, profile: dict | None) -> str:
    """The page language: the flag, else the profile's preference, else Chinese."""
    chosen = normalize_language(cli)
    if chosen:
        return chosen
    identity = profile.get("identity_and_language") if isinstance(profile, dict) else None
    if isinstance(identity, dict):
        preferred = normalize_language(identity.get("preferred_response_language"))
        if preferred:
            return preferred
    return "zh"


def pick(lang: str, zh: str, en: str) -> str:
    """The message in `lang`. An unknown language is a caller bug, so it raises instead of
    quietly answering in Chinese."""
    if lang not in LANGUAGES:
        raise ValueError(f"unknown intake language {lang!r}; expected one of {LANGUAGES}")
    return en if lang == "en" else zh
