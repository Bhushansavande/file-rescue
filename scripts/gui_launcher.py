"""Entry point for the windowed Windows build (PyInstaller): opens the File Rescue window."""
from filerescue import gui

if __name__ == "__main__":
    raise SystemExit(gui.run())
