import subprocess
import sys

from tidal.fix_ghc_env import dedupe

ENV = """clear-package-db
global-package-db
package-db /home/u/.cabal/store/ghc-9.4.7/package.db
package-id base-4.17.2.0
package-id tidal-1.10.1-4f1c2a
package-id tidal-link-1.1.0-9ab
package-id tidal-1.10.1-7e9d0b
package-id tidal-link-1.1.0-9ab
"""


def test_dedupe_keeps_last_entry_per_package():
    kept, removed = dedupe(ENV.splitlines())
    assert removed == ["tidal-1.10.1-4f1c2a", "tidal-link-1.1.0-9ab"]
    assert kept == ["clear-package-db", "global-package-db",
                    "package-db /home/u/.cabal/store/ghc-9.4.7/package.db",
                    "package-id base-4.17.2.0", "package-id tidal-1.10.1-7e9d0b",
                    "package-id tidal-link-1.1.0-9ab"]


def test_script_rewrites_file_with_backup(tmp_path):
    env = tmp_path / "default"
    env.write_text(ENV)
    out = subprocess.run([sys.executable, "tidal/fix_ghc_env.py", str(env)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert env.read_text().count("package-id tidal-1.10.1") == 1
    assert list(tmp_path.glob("default.bak-*"))
    again = subprocess.run([sys.executable, "tidal/fix_ghc_env.py", str(env)], capture_output=True, text=True)
    assert "no duplicate" in again.stdout
