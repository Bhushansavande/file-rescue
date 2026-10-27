"""Entry point used to build the standalone Windows executable with PyInstaller."""

import sys

from filerescue.cli import main

if __name__ == "__main__":
    sys.exit(main())
