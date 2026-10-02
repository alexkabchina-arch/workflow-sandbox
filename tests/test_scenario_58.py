"""Сценарий workflow#58 (а): заведомо красный тест. Не сливать."""

import unittest


class Scenario58Test(unittest.TestCase):
    def test_red(self):
        self.fail("сценарий workflow#58: тест заведомо красный")
