"""GET /_/version reports the commit the running process was built from."""

import unittest
from unittest.mock import patch

from api.routes.health import get_commit


class CommitEndpointTest(unittest.TestCase):
    def test_reports_the_baked_in_commit(self):
        with patch.dict("os.environ", {"GIT_SHA": "79b2da0abc"}):
            self.assertEqual({"commit": "79b2da0abc"}, get_commit())

    def test_says_unknown_rather_than_guessing(self):
        # An image built without --build-arg GIT_SHA must not pass a commit check.
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual({"commit": "unknown"}, get_commit())

    def test_is_routed_publicly_next_to_health(self):
        from api.routes.health import health_router

        self.assertIn("/_/version", {r.path for r in health_router.routes})


if __name__ == "__main__":
    unittest.main()
