"""Localize terminal messages while preserving user paths and media filenames.

English is the source language in the entry scripts. Chinese translations live in
zh_messages.json. The older English catalog remains a fallback for Chinese
recognition terms that are deliberately kept in matching rules and metadata.
"""
from __future__ import annotations

import argparse
import builtins
import json
from pathlib import Path
import re
import sys
from typing import TextIO

_LANGUAGE = "en"
_CATALOGS: dict[str, dict[str, str]] = {}
_PATTERNS: dict[str, re.Pattern[str]] = {}


def _prepare(language: str) -> None:
    if language in _CATALOGS:
        return
    filename = "zh_messages.json" if language == "zh" else "en_messages.json"
    catalog = json.loads(Path(__file__).with_name(filename).read_text(encoding="utf-8"))
    if language == "zh":
        catalog = {key: value for key, value in catalog.items() if len(key) >= 4 and key != value}
    else:
        catalog = {key: value for key, value in catalog.items() if key != value}
    _CATALOGS[language] = catalog
    # Complete templates are translated before formatting; they do not belong in
    # the fallback matcher for already rendered terminal text.
    keys = sorted(
        (key for key in catalog if language != "zh" or "{" not in key),
        key=len, reverse=True,
    )
    if language == "zh":
        parts = []
        for key in keys:
            left = r"(?<![A-Za-z0-9])" if key[0].isalnum() else ""
            right = r"(?![A-Za-z0-9])" if key[-1].isalnum() else ""
            parts.append(left + re.escape(key) + right)
    else:
        parts = [re.escape(key) for key in keys]
    _PATTERNS[language] = re.compile("|".join(parts))


def _preserve_media_paths(text: str) -> tuple[str, list[str]]:
    if "/" not in text:
        return text, []
    paths: list[str] = []
    pattern = re.compile(r"(?:(?<!\S)|(?<=[：:(]))/(?=[^\s])[^\r\n；，。：（）]*")

    def hide(match: re.Match[str]) -> str:
        value = match.group()
        trailing_message = ""
        for suffix in (" 内", " 下"):
            if value.endswith(suffix):
                value, trailing_message = value[:-len(suffix)], suffix
                break
        paths.append(value)
        return f"\x00PATH{len(paths) - 1}\x00{trailing_message}"

    return pattern.sub(hide, text), paths


def _translate(text: str, language: str) -> str:
    _prepare(language)
    protected, paths = _preserve_media_paths(text)
    pattern = _PATTERNS[language]
    catalog = _CATALOGS[language]
    translated = pattern.sub(lambda match: catalog[match.group()], protected)
    for index, path in enumerate(paths):
        translated = translated.replace(f"\x00PATH{index}\x00", path)
    return translated


def translate_terminal_text(text: str) -> str:
    if not isinstance(text, str):
        return text
    if _LANGUAGE == "en" and not re.search(r"[\u3400-\u9fff]", text):
        return text
    return _translate(text, _LANGUAGE)


def localized_message(message: str) -> str:
    if _LANGUAGE == "en":
        return message
    _prepare("zh")
    return _CATALOGS["zh"].get(message, message)


def localized_format(message: str, **values: object) -> str:
    """Translate a whole template before inserting paths and other user data."""
    return localized_message(message).format(**values)


class _LocalizedStream:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, message: str) -> int:
        self._stream.write(translate_terminal_text(message))
        return len(message)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def install_terminal_language(language: str) -> None:
    global _LANGUAGE
    _LANGUAGE = language
    _prepare(language)
    if not isinstance(sys.stdout, _LocalizedStream):
        sys.stdout = _LocalizedStream(sys.stdout)
    if not isinstance(sys.stderr, _LocalizedStream):
        sys.stderr = _LocalizedStream(sys.stderr)


def localized_input(prompt: str = "") -> str:
    return builtins.input(translate_terminal_text(prompt))


class LocalizedArgumentParser(argparse.ArgumentParser):
    """Translate help before argparse wraps it into terminal lines."""

    def __init__(self, *args, **kwargs):
        for field in ("description", "epilog"):
            if isinstance(kwargs.get(field), str):
                kwargs[field] = translate_terminal_text(kwargs[field])
        super().__init__(*args, **kwargs)

    def add_argument(self, *args, **kwargs):
        if isinstance(kwargs.get("help"), str):
            kwargs["help"] = translate_terminal_text(kwargs["help"])
        return super().add_argument(*args, **kwargs)
