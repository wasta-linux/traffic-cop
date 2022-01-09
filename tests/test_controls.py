import gi
import psutil
import subprocess
import sys
import unittest

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
from pathlib import Path

from trafficcop import app
#from trafficcop import config
from trafficcop import utils

# Assert*() methods here:
# https://docs.python.org/3/library/unittest.html?highlight=pytest#unittest.TestCase

class ActivationToggle(unittest.TestCase):
    def setUp(self):
        self.app = app.app
        self.stop = ['systemctl', 'stop', 'traffic-cop.service']
        self.start = ['systemctl', 'start', 'traffic-cop.service']

    def test_activate_UNFINISHED(self):
        pass
        '''
        # 1. The app needs to see the service as deactivated.
        run = subprocess.run(self.stop)
        self.app.run('traffic-cop')
        toggle = self.app.toggle_active
        self.assertFalse(toggle.get_state())

        # 2. The toggle needs to be toggled to "True".
        toggled = toggle.set_state(True)
        self.assertTrue(toggle.get_state())

        # 3. The service should be activated.
        pid, time = utils.wait_for_tt_start()
        self.assertTrue(pid)

        # 4. The final toggle state needs to match the service state.
        # 5. Ensure that service is started afterwards.
        for proc in psutil.process_iter(attrs=['pid', 'name', 'exe']):
            if proc.exe() == '/usr/bin/traffic-cop':
                proc.kill()
                break
        run = subprocess.run(self.start)
        '''

    def test_deactivate_UNFINISHED(self):
        pass

    def tearDown(self):
        pass

'''
class RestartButton(unittest.TestCase):
    def setUp(self):
        pass

    def test_deactivated_click(self):
        pass

    def test_activated_click(self):
        pass

    def tearDown(self):
        pass

class AutorunToggle(unittest.TestCase):
    def setUp(self):
        pass

    def test_enable(self):
        pass

    def test_disable(self):
        pass

    def tearDown(self):
        pass

class LogButton(unittest.TestCase):
    def setUp(self):
        pass

    def test_deactivated_click(self):
        pass

    def test_activated_click(self):
        pass

    def tearDown(self):
        pass

class ApplyButton(unittest.TestCase):
    def setUp(self):
        pass

    def test_not_changed_click(self):
        pass

    def test_changed_click(self):
        pass

    def tearDown(self):
        pass

class EditButton(unittest.TestCase):
    def setUp(self):
        pass

    def test_click(self):
        pass

    def tearDown(self):
        pass

class ResetButton(unittest.TestCase):
    def setUp(self):
        pass

    def test_default_config(self):
        pass

    def test_user_config(self):
        pass

    def tearDown(self):
        pass
'''
