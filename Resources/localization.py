"""Translate fixed error text at the protocol boundary, leaving user values intact."""
import json
import pathlib

CATALOG = json.loads(pathlib.Path(__file__).with_name('localizations.json').read_text())


def message(value, language):
    if language != 'en' or not isinstance(value, str):
        return value
    if value in CATALOG:
        return CATALOG[value]
    for key in sorted((key for key in CATALOG if key.endswith(': ')), key=len, reverse=True):
        if value.startswith(key):
            return CATALOG[key] + value[len(key):]
    return value


def reply(value, language):
    result = dict(value)
    for key in ('error', 'updateError'):
        if key in result: result[key] = message(result[key], language)
    return result
