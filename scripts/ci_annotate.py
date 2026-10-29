"""Run a command; if it fails, show the tail of its output as a GitHub error annotation.

Usage: python scripts/ci_annotate.py <command> [args...]
(The raw log of a failed step is hard to read from outside the Actions page; annotations are not.)
"""
import subprocess
import sys

proc = subprocess.run(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
sys.stdout.write(proc.stdout)
if proc.returncode:
    tail = proc.stdout[-3500:].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::error title=Command failed ({' '.join(sys.argv[1:3])})::{tail}")
sys.exit(proc.returncode)
