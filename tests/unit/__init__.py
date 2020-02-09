import unittest

from swift_zipkin import zipkin


class TestBase(unittest.TestCase):

    def test_placeholder(self):
        self.assertTrue(zipkin)
