"""Проба позднего конфликта (workflow#157): CI PR спит до срока; локально и в main — пропуск."""

import os
import time
import unittest

DEADLINE = 1791978000  # 14.10.2026 11:40 UTC


@unittest.skipUnless(
    os.environ.get("GITHUB_HEAD_REF", "").startswith("ticket-124"), "только CI PR тикета 124"
)
class SlowProbe(unittest.TestCase):
    def test_sleep_until_deadline(self):
        time.sleep(max(0, min(DEADLINE - time.time(), 5400)))
