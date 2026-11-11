"""The scan rules are spring-pr-review's: the copies here must stay byte-for-byte equal.

Edit them in plugins/spring-pr-review/skills/review-pr/scripts/, then copy them here. Skipped when
the plugin is installed on its own (no sibling spring-pr-review folder).
"""

import os
import unittest

import helpers

SHARED = ("pr_scan.py", "env_overrides.py", "plugin_settings.py")
UPSTREAM = os.path.abspath(os.path.join(helpers.SCRIPTS, "..", "..", "..", "..", "spring-pr-review", "skills",
                                        "review-pr", "scripts"))


@unittest.skipUnless(os.path.isdir(UPSTREAM), "spring-pr-review is not next to this plugin")
class VendoredTest(unittest.TestCase):
    def test_shared_scripts_are_identical(self):
        for name in SHARED:
            with self.subTest(name=name):
                with open(os.path.join(helpers.SCRIPTS, name), "rb") as mine, \
                        open(os.path.join(UPSTREAM, name), "rb") as upstream:
                    self.assertEqual(mine.read(), upstream.read(),
                                     f"{name} differs from spring-pr-review's copy: copy it again")


if __name__ == "__main__":
    unittest.main()
