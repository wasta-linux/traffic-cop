import unittest
from pathlib import Path

from trafficcop import utils

# Assert*() methods here:
# https://docs.python.org/3/library/unittest.html?highlight=pytest#unittest.TestCase

class Misc(unittest.TestCase):
    def setUp(self):
        self.tests_dir = Path(__file__).parent
        self.pkg_dir = self.tests_dir.parent
        self.example_config = self.tests_dir / 'data' / 'traffic-cop.yaml.example'

    def test_check_diff(self):
        example = self.example_config
        default = self.tests_dir / 'data' / 'traffic-cop.yaml.default'
        diff = utils.check_diff(example, default)
        self.assertNotEqual(diff, 0)

    def test_ensure_config_backup(self):
        # This only tests that tests/data/traffic-cop.yaml == tests/data/traffic-cop.yaml.bak.
        #   It doesn't test the creation of a properly-named new backup file.
        current = self.tests_dir / 'data' / 'traffic-cop.yaml'
        result = utils.mute(utils.ensure_config_backup, current)
        self.assertTrue(result)

    def tearDown(self):
        pass

class Timestamps(unittest.TestCase):
    def setUp(self):
        self.convert = utils.convert_human_to_epoch

    def test_a_older(self):
        epochA = self.convert("Sat Oct 17 05:32:38 2020")
        epochB = self.convert("Sun Oct 18 05:32:38 2020")
        self.assertGreater(epochB, epochA)

    def test_b_older(self):
        epochA = self.convert("Sat Oct 17 05:32:38 2020")
        epochB = self.convert("Thu Sep 17 05:32:38 2020")
        self.assertGreater(epochA, epochB)

    def test_bad(self):
        epoch = utils.mute(self.convert, "Lun Sep 17 00:00:00 2020")
        self.assertEqual(epoch, "")

    def test_empty(self):
        epoch = self.convert("")
        self.assertEqual(epoch, "")

    def tearDown(self):
        pass

class TtInfo(unittest.TestCase):
    def setUp(self):
        # exe must have at least 3 commandline tokens for the function to work.
        self.valid_exe = '/usr/bin/networkd-dispatcher'
        self.invalid_exe = '/dev/null'

    def test_tt_off(self):
        info = utils.get_tt_info(exe=self.invalid_exe)
        self.assertEqual(info[0], -1)

    def test_tt_on(self):
        info = utils.get_tt_info(exe=self.valid_exe)
        self.assertGreater(info[0], 0)

    def test_wait_for_tt_fail(self):
        info = utils.wait_for_tt_start(exe=self.invalid_exe, max=5)
        self.assertEqual(info[0], -1)

    def test_wait_for_tt_pass(self):
        info = utils.wait_for_tt_start(exe=self.valid_exe, max=5)
        self.assertGreater(info[0], 0)

    def tearDown(self):
        pass
