import unittest
#from pathlib import Path

from trafficcop import app
from trafficcop import config
from trafficcop import handler
from trafficcop import utils
from trafficcop import worker
# from . import app
# from . import config
# from . import handler
# from . import utils
# from . import worker

# Assert*() methods here:
# https://docs.python.org/3/library/unittest.html?highlight=pytest#unittest.TestCase


class App(unittest.TestCase):
    def setUp(self):
        self.app = app.TrafficCop()

    def test_get_config_files(self):
        file_list = self.app.get_config_files()
        self.assertTrue(len(file_list) > 0)

    def tearDown(self):
        pass
