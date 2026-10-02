"""Сценарий workflow#58 (а2, повтор после integration_id): заведомо красный тест, CI пропущен. Не сливать."""

import unittest


class Scenario58Test(unittest.TestCase):
    def test_red(self):
        self.fail("сценарий workflow#58: тест заведомо красный")
