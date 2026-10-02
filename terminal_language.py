"""Translate program-owned terminal messages without changing sorting decisions.

English strings are prepared offline and shipped with the project. Translation
never runs against media files, reports, or config values; Chinese mode leaves
standard streams untouched.
"""
from __future__ import annotations

import argparse
import builtins
import json
from pathlib import Path
import re
import sys
from typing import TextIO

_CATALOG: dict[str, str] | None = None
_PATTERN: re.Pattern[str] | None = None
_ENABLED = False


def _prepare() -> None:
    global _CATALOG, _PATTERN
    if _CATALOG is not None:
        return
    source = Path(__file__).with_name("en_messages.json")
    catalog = json.loads(source.read_text(encoding="utf-8"))
    _CATALOG = {key: value for key, value in catalog.items() if value and key != value}
    _PATTERN = re.compile("|".join(re.escape(key) for key in sorted(_CATALOG, key=len, reverse=True)))


def _preserve_media_paths(text: str) -> tuple[str, list[str]]:
    if "/" not in text:
        return text, []
    paths: list[str] = []
    # Paths may come from user input outside BACKUP_ROOT in validation errors.
    # A slash inside ordinary prose (such as XMP/AAE) is not a path boundary.
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


def translate_terminal_text(text: str) -> str:
    if not _ENABLED or not isinstance(text, str) or not re.search(r"[\u3400-\u9fff]", text):
        return text
    _prepare()
    protected, paths = _preserve_media_paths(text)
    assert _PATTERN is not None and _CATALOG is not None
    translated = _PATTERN.sub(lambda match: _CATALOG[match.group()], protected)
    for index, path in enumerate(paths):
        translated = translated.replace(f"\x00PATH{index}\x00", path)
    return translated


class _EnglishStream:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        self._stream.write(translate_terminal_text(text))
        return len(text)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def install_terminal_language(language: str) -> None:
    global _ENABLED
    if language != "en":
        return
    _ENABLED = True
    _prepare()
    if not isinstance(sys.stdout, _EnglishStream):
        sys.stdout = _EnglishStream(sys.stdout)
    if not isinstance(sys.stderr, _EnglishStream):
        sys.stderr = _EnglishStream(sys.stderr)


def localized_input(prompt: str = "") -> str:
    return builtins.input(translate_terminal_text(prompt))


class LocalizedArgumentParser(argparse.ArgumentParser):
    """Translate help text before argparse wraps it into terminal lines."""

    def __init__(self, *args, **kwargs):
        for field in ("description", "epilog"):
            if isinstance(kwargs.get(field), str):
                kwargs[field] = translate_terminal_text(kwargs[field])
        super().__init__(*args, **kwargs)

    def add_argument(self, *args, **kwargs):
        if isinstance(kwargs.get("help"), str):
            kwargs["help"] = translate_terminal_text(kwargs["help"])
        return super().add_argument(*args, **kwargs)
