#!/bin/zsh
set -eu
cd "${0:A:h}"
export PYTHONDONTWRITEBYTECODE=1
/usr/bin/python3 -m unittest discover -s Tests -v
private_python="outputs/libvpn.app/Contents/Resources/Python/bin/python3"
if [[ -x "$private_python" ]]; then
  "$private_python" -I -S -B -m unittest discover -s Tests -v
fi

packaging_python="work/dmg-venv/bin/python3"
if [[ -x "$packaging_python" ]]; then
  "$packaging_python" -B -m unittest discover -s Tests -p test_dmg_layout.py -v
fi

if [[ "$(uname -s)" == Darwin ]]; then
  mkdir -p work/tests
  xcrun swiftc -swift-version 5 Source/Localization.swift Source/Models.swift Source/Runtime.swift Source/ProfileFile.swift Tests/ModelChecks.swift -o work/tests/models
  work/tests/models
  /usr/bin/python3 - <<'PYLOCAL'
import os, pathlib, plistlib, shutil, subprocess
app=pathlib.Path('work/tests/Localization.app')
if app.exists(): shutil.rmtree(app)
binary=app/'Contents/MacOS';binary.mkdir(parents=True)
resources=app/'Contents/Resources';resources.mkdir()
shutil.copy2('work/tests/models',binary/'models')
(app/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleExecutable='models', CFBundleIdentifier='local.fennec.localization.tests', CFBundleDevelopmentRegion='en', CFBundleLocalizations=['en','ru'])))
for language in ('en','ru'): shutil.copytree(pathlib.Path('Resources')/(language+'.lproj'), resources/(language+'.lproj'))
for preferences,expected in [('(en)','en'),('(ru)','ru'),('(de)','en')]:
    subprocess.run([str(binary/'models'),'-AppleLanguages',preferences],env=dict(os.environ,FENNEC_TEST_LANGUAGE=expected),check=True)
PYLOCAL
fi
