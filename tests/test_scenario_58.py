"""Сценарий workflow#58 (б): заведомо красный тест, мёрж администратора без --admin. Не сливать."""

import unittest


class Scenario58Test(unittest.TestCase):
    def test_red(self):
        self.fail("сценарий workflow#58: тест заведомо красный")
