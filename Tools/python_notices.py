"""Extract upstream build metadata and licenses with the pinned CPython."""
import pathlib
import sys
import tarfile


def extract(archive, destination):
    count = 0
    with tarfile.open(archive, 'r:zst') as package:
        for member in package:
            parts = pathlib.PurePosixPath(member.name)
            if member.isfile() and (member.name == 'python/PYTHON.json' or member.name.startswith('python/licenses/')):
                if '..' in parts.parts or parts.is_absolute():
                    raise ValueError('Unsafe notice path')
                path = destination.joinpath(*parts.parts[1:])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(package.extractfile(member).read())
                path.chmod(0o644)
                count += 1
    if count < 2 or not (destination / 'PYTHON.json').is_file():
        raise ValueError('Python dependency notices are missing')


if __name__ == '__main__':
    extract(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
