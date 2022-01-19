import unittest

from pathlib import Path

from trafficcop import config
from trafficcop import utils

# Assert*() methods here:
# https://docs.python.org/3/library/unittest.html?highlight=pytest#unittest.TestCase


class Yaml(unittest.TestCase):
    def setUp(self):
        tests_dir = Path(__file__).parent
        self.data_dir = tests_dir / 'data'

    def test_convert_bad_syntax(self):
        yaml = self.data_dir / 'bad_syntax.yaml'
        store = config.convert_yaml_to_store(yaml)
        self.assertNotEqual(store, '')
        for row in store:
            # Ensure row has the correct number of columns.
            self.assertEqual(len(row[:]), 13)

    def test_convert_default(self):
        yaml = self.data_dir / 'traffic-cop.yaml.default'
        store = config.convert_yaml_to_store(yaml)
        # Ensure store is not empty.
        self.assertNotEqual(store, '')
        for row in store:
            # Ensure row has 7 columns.
            self.assertEqual(len(row[:]), 13)

    def test_convert_empty(self):
        yaml = self.data_dir / 'empty.yaml'
        store = utils.mute(config.convert_yaml_to_store, yaml)
        self.assertEqual(store, '')

    def tearDown(self):
        pass

class Bytes(unittest.TestCase):
    def setUp(self):
        pass

    def test_convert_config_rates_to_human(self):
        rates = [
            '4bit',
            '8bit',
            '16kbit',
            '23kbit',
            '8kbps',
            '16kbps',
            '3mbit',
            '3mibps',
            '7gibit',
        ]

        outputs = [
            ['0', 'B/s'],
            ['1', 'B/s'],
            ['2', 'KB/s'],
            ['3', 'KB/s'],
            ['8', 'KB/s'],
            ['16', 'KB/s'],
            ['375', 'KB/s'],
            ['3', 'MB/s'],
            ['854', 'MB/s'],
        ]
        for i in range(len(rates)):
            human = config.convert_config_rates_to_human(rates[i])
            self.assertEqual(outputs[i], human)

    def tearDown(self):
        pass
