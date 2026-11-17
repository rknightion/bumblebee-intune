import os
import unittest


class CITimeoutProof(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("GITHUB_ACTIONS") == "true", "Actions proof only")
    def test_required_job_reports_failure(self):
        self.fail("intentional CI aggregate proof failure")
