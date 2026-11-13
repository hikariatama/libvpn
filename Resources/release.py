"""Public helper identity used to detect an app/helper update."""
import json
import pathlib


def helper_build():
    try:
        value = json.loads(pathlib.Path(__file__).with_name('helper-build.json').read_text())
        return value['build']
    except (OSError, ValueError, KeyError):
        return 'development'
