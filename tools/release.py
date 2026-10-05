#!/usr/bin/env python3
"""Start a new version: tools/release.py 0.5.0

Sets the version in mailman/config.yaml and pyproject.toml (and uv.lock), and opens a section for it in
mailman/CHANGELOG.md. Fill that section in, commit and push: CI builds the image, tags the commit v0.5.0 and
publishes the release. Which number to raise is described in the README under "Versions".
"""
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit("usage: tools/release.py MAJOR.MINOR.PATCH")
    new = sys.argv[1]
    manifest, project, log = ROOT / "mailman/config.yaml", ROOT / "pyproject.toml", ROOT / "mailman/CHANGELOG.md"
    old = re.search(r'^version: "?([^"\n]+)"?$', manifest.read_text(), re.M).group(1)
    if tuple(map(int, new.split("."))) <= tuple(map(int, old.split("."))):
        sys.exit(f"{new} is not above the current version {old}")
    manifest.write_text(re.sub(r'^version: .*$', f'version: "{new}"', manifest.read_text(), count=1, flags=re.M))
    project.write_text(re.sub(r'^version = .*$', f'version = "{new}"', project.read_text(), count=1, flags=re.M))
    text = log.read_text()
    section = f"## [{new}] - {datetime.date.today().isoformat()}\n\n### Added\n\n### Changed\n\n### Fixed\n\n"
    at = text.index("## [")
    log.write_text(text[:at] + section + text[at:])
    subprocess.run(["uv", "lock"], cwd=ROOT, check=True)
    print(f"{old} → {new}. Now write the {new} section of mailman/CHANGELOG.md, commit and push.")


if __name__ == "__main__":
    main()
