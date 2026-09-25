#!/usr/bin/env python3
"""
Repair a GHC package environment that lists the same package twice.

Symptom (when GHCi loads BootTidal.hs):

    Ambiguous module name 'Sound.Tidal.Context':
      it was found in multiple packages: tidal-1.10.1 tidal-1.10.1

This happens when `cabal install --lib tidal` runs more than once: each run
appends another `package-id tidal-...` line to
~/.ghc/<arch>-<os>-<ghc-version>/environments/default. GHCi then sees two
copies of every Tidal module and refuses to import either.

This script keeps only the most recently added entry for each package (the
last line wins, which is what cabal just installed), writes a backup next to
the file, and prints what it removed.

    python3 tidal/fix_ghc_env.py            # fix the default environment
    python3 tidal/fix_ghc_env.py --dry-run  # only show what would change
    python3 tidal/fix_ghc_env.py PATH       # fix a specific environment file
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time

# package-id lines look like "package-id tidal-1.10.1-<hash>" or
# "package-id tidal-link-1.1.0-inplace"; the package name is everything
# before the first "-<digits>" version component.
_PKG_RE = re.compile(r"^package-id\s+(?P<id>(?P<name>[A-Za-z][A-Za-z0-9-]*?)-\d+(?:\.\d+)*(?:-\S+)?)\s*$")


def default_env_file() -> str | None:
    try:
        version = subprocess.run(["ghc", "--numeric-version"], capture_output=True, text=True,
                                 check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        version = "*"
    matches = sorted(glob.glob(os.path.expanduser(f"~/.ghc/*-{version}/environments/default")))
    return matches[-1] if matches else None


def dedupe(lines: list[str]) -> tuple[list[str], list[str]]:
    """Return (new_lines, removed_ids), keeping the last entry per package name."""
    last_index: dict[str, int] = {}
    for i, line in enumerate(lines):
        m = _PKG_RE.match(line.strip())
        if m:
            last_index[m.group("name")] = i
    kept, removed = [], []
    for i, line in enumerate(lines):
        m = _PKG_RE.match(line.strip())
        if m and last_index[m.group("name")] != i:
            removed.append(m.group("id"))
            continue
        kept.append(line)
    return kept, removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", help="environment file (default: ~/.ghc/*/environments/default)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = args.path or default_env_file()
    if not path or not os.path.exists(path):
        print("No GHC environment file found (nothing to fix).")
        return 0
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    kept, removed = dedupe(lines)
    if not removed:
        print(f"{path}: no duplicate packages.")
        return 0
    print(f"{path}: removing duplicate entries:")
    for pkg in removed:
        print(f"  - {pkg}")
    if args.dry_run:
        return 0
    backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
    print(f"Fixed. Backup saved as {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
