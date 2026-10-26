"""Create signed helper-only archives. The private release key never enters a bundle."""
import hashlib
import json
import os
import pathlib
import subprocess
import zipfile


def create(resources, private_key, output):
    resources, output = pathlib.Path(resources), pathlib.Path(output)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'helper.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as packed:
        for path in sorted(resources.rglob('*')):
            relative = path.relative_to(resources)
            if relative.parts[0] in ('HelperUpdate', 'third-party') or relative.suffix == '.lproj':
                continue
            if path.is_file() and (relative.parts[0] == 'Python' or len(relative.parts) == 1 and
                                  (path.suffix in ('.py', '.json', '.txt') or path.name in ('sing-box', 'PackageSignature'))):
                if path.is_symlink() and not path.resolve().is_relative_to(resources.resolve()):
                    raise ValueError('Helper archive symlink escapes its resources')
                info = zipfile.ZipInfo(str(relative));info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100755 if os.access(path, os.X_OK) else 0o100644) << 16
                packed.writestr(info, path.read_bytes())
    release = json.loads((resources / 'update-release.json').read_text())
    manifest = dict(release, schemaVersion=1, size=archive.stat().st_size,
                    sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    path = output / 'manifest.json'
    path.write_text(json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n')
    subprocess.run([str(resources / 'PackageSignature'), 'sign', str(private_key), str(path), str(output / 'signature.txt')], check=True)
    subprocess.run([str(resources / 'PackageSignature'), 'verify', str(resources / 'update-public-key.txt'), str(path), str(output / 'signature.txt')], check=True)
    return manifest
