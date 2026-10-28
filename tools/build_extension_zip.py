"""Build the installable extension zip without Blender (used by the GitHub release workflow).

Same layout as `blender --command extension build`: the files of cache_play/ at the root of
cache_play-<version>.zip, leaving out __pycache__/, *.pyc and dot files. Prints the zip path.

    python tools/build_extension_zip.py [output_dir]
"""

import os
import sys
import tomllib
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "cache_play")


def version():
    with open(os.path.join(PACKAGE, "blender_manifest.toml"), "rb") as handle:
        manifest = tomllib.load(handle)
    for field in ("id", "version", "name", "blender_version_min"):
        if not manifest.get(field):
            sys.exit(f"blender_manifest.toml has no {field}")
    return manifest["version"]


def files():
    for folder, dirs, names in os.walk(PACKAGE):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.startswith("."))
        for name in sorted(names):
            if name.endswith((".pyc", ".zip")) or name.startswith("."):
                continue
            path = os.path.join(folder, name)
            yield path, os.path.relpath(path, PACKAGE).replace(os.sep, "/")


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"cache_play-{version()}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for source, name in files():
            archive.write(source, name)
    print(path)


if __name__ == "__main__":
    main()
