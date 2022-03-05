import logging
import unittest

from pathlib import Path

from trafficcop import config
from trafficcop import utils

# Assert*() methods here:
# https://docs.python.org/3/library/unittest.html?highlight=pytest#unittest.TestCase


class Yaml(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        tests_dir = Path(__file__).parent
        self.data_dir = tests_dir / 'data'
        self.fallback_file = self.data_dir / 'traffic-cop.yaml.default'
        default_yaml_file = self.data_dir / 'traffic-cop.yaml.default'
        self.default_store = config.convert_yaml_to_store(default_yaml_file, self.fallback_file, test=True)

    def test_bad_syntax_empty_file(self):
        yaml_file = self.data_dir / 'empty.yaml'
        status = config.validate_yaml(yaml_file)
        self.assertFalse(status)

    def test_bad_syntax_indent(self):
        yaml_file = self.data_dir / 'bad_syntax_indent.yaml'
        status = config.validate_yaml(yaml_file)
        self.assertFalse(status)

    def test_bad_syntax_no_match(self):
        yaml_file = self.data_dir / 'bad_syntax_no_match.yaml'
        status = config.validate_yaml(yaml_file)
        self.assertFalse(status)

    def test_bad_syntax_wrong_parameter(self):
        yaml_file = self.data_dir / 'bad_syntax_wrong_parameter.yaml'
        status = config.validate_yaml(yaml_file)
        self.assertFalse(status)

    def test_file_no_exist(self):
        yaml_file = self.data_dir / 'nonexistent_file.yaml'
        status = config.validate_yaml(yaml_file)
        self.assertFalse(status)

    def test_file_good_syntax(self):
        yaml_file = self.data_dir / 'traffic-cop.yaml.default'
        status = config.validate_yaml(yaml_file)
        self.assertTrue(status)

    def test_convert_bad_syntax(self):
        yaml_file = self.data_dir / 'bad_syntax.yaml'
        store = config.convert_yaml_to_store(yaml_file, self.fallback_file, test=True)
        self.assertNotEqual(store, '')
        for row in store:
            # Ensure row has the correct number of columns.
            self.assertEqual(len(row[:]), 13)

    def test_convert_default(self):
        # Ensure store is not empty.
        self.assertNotEqual(self.default_store, '')
        for row in self.default_store:
            # Ensure row has 7 columns.
            self.assertEqual(len(row[:]), 13)

    def test_create_treeview(self):
        tree = config.create_config_treeview(self.default_store)
        self.assertTrue(tree)

    @unittest.skip('Needs work.')
    def test_update_config_store(self):
        new = config.update_config_store(None, self.default_store)
        self.assertNotEqual(None, new)

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
