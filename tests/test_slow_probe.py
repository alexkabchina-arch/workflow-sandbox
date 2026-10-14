"""Проба позднего конфликта (workflow#157): CI PR спит до срока; локально и в main — пропуск."""

import os
import time
import unittest

DEADLINE = 1791972726  # 14.10.2026 10:12 UTC


@unittest.skipUnless(
    os.environ.get("GITHUB_HEAD_REF", "").startswith("ticket-114"), "только CI PR тикета 114"
)
class SlowProbe(unittest.TestCase):
    def test_sleep_until_deadline(self):
        time.sleep(max(0, min(DEADLINE - time.time(), 5400)))
