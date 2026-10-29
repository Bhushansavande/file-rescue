"""GUI helpers that need no display."""

import unittest

from filerescue import gui


class DriveList(unittest.TestCase):
    def test_list_drives_never_raises(self):
        drives = gui.list_drives()
        self.assertIsInstance(drives, list)
        for label, path in drives:
            self.assertTrue(label and path)


if __name__ == "__main__":
    unittest.main()
