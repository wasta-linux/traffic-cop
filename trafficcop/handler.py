""" Signal handler module. """

import logging
import subprocess
import threading
from pathlib import Path

from . import utils
from . import worker


class Handler():
    def __init__(self, app):
        self.app = app

    def gtk_widget_destroy(self, *args):
        #print(threading.enumerate(), 'threads')
        self.app.quit()

    def on_toggle_unit_state_state_set(self, widget, state):
        # Apply new state to the service.
        if state == True:
            cmd = ["systemctl", "enable", "traffic-cop.service"]
        elif state == False:
            cmd = ["systemctl", "disable", "traffic-cop.service"]
        subprocess.run(cmd)
        # Ensure that toggle button matches true state.
        self.app.update_state_toggles()

    def on_toggle_active_state_set(self, widget, state):
        # Apply new state to the service.
        if state == True:
            self.app.start_service()
        elif state == False:
            self.app.stop_service()

    def on_button_restart_clicked(self, folder_obj):
        self.app.restart_service()

    def on_button_log_clicked(self, *args):
        target = worker.handle_button_log_clicked
        t_log = threading.Thread(name='T-log', target=target, args=(self.app,))
        t_log.start()

    def on_button_config_clicked(self, *args):
        # NOTE: Button later renamed to "Edit..."
        # # Ensure that backup is made of current config.
        # logging.debug('Ensuring backup of current config.')
        # current = Path("/etc/traffic-cop.yaml")
        # utils.ensure_config_backup(current)

        # # Update fallback config file.
        # self.app.fallback_config = self.app.get_config_files()[0]

        target = worker.handle_button_config_clicked
        t_config = threading.Thread(name='T-cfg', target=target)
        t_config.start()
        # Set apply button to "sensitive".
        self.app.button_apply.set_sensitive(True)

        #target = worker.handle_config_changed
        #t_restart = threading.Thread(target=target)
        #t_restart.start()

    def on_button_apply_clicked(self, button):
        # Update the config file variable.
        self.app.config_file = Path('/etc/traffic-cop.yaml')
        # Restart the service to apply updated configuration.
        self.app.restart_service()
        # Disable the button again.
        button.set_sensitive(False)

    def on_button_reset_clicked(self, button):
        current = Path("/etc/traffic-cop.yaml")
        default = self.app.default_config

        # Get user confirmation before resetting configuration.
        approved = self.app.get_user_confirmation()
        if not approved:
            return

        # First check if current config matches default config.
        diff = utils.check_diff(current, default)
        if diff == 0:
            # Already using the default config.
            logging.debug("Using default config.")
            return

        # # Ensure that backup is made of current config.
        # logging.debug('Ensuring backup of current config.')
        # utils.ensure_config_backup(current)

        # Copy /usr/share/traffic-cop/traffic-cop.yaml.default to /etc/traffic-cop.yaml;
        #   overwrite existing file.
        logging.debug('Setting config file to default.')
        p = subprocess.run(['sudo', '/usr/bin/traffic-cop', '--reset'])
        # Restart the service to apply default configuration.
        self.app.restart_service()
