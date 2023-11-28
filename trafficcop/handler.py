""" Signal handler module. """

import logging
import shutil
import subprocess
import threading
from pathlib import Path

# from trafficcop import app
# from trafficcop import utils
# from trafficcop import worker
from . import app
from . import utils
from . import worker


class Handler():
    def gtk_widget_destroy(self, *args):
        #print(threading.enumerate(), 'threads')
        app.app.quit()

    def on_toggle_unit_state_state_set(self, widget, state):
        # Apply new state to the service.
        if state == True:
            cmd = ["systemctl", "enable", "traffic-cop.service"]
        elif state == False:
            cmd = ["systemctl", "disable", "traffic-cop.service"]
        subprocess.run(cmd)
        # Ensure that toggle button matches true state.
        app.app.update_state_toggles()

    def on_toggle_active_state_set(self, widget, state):
        # Apply new state to the service.
        if state == True:
            app.app.start_service()
        elif state == False:
            app.app.stop_service()

    def on_button_restart_clicked(self, folder_obj):
        app.app.restart_service()

    def on_button_log_clicked(self, *args):
        target = worker.handle_button_log_clicked
        t_log = threading.Thread(target=target, name='T-log')
        t_log.start()

    def on_button_config_clicked(self, *args):
        # NOTE: Button later renamed to "Edit..."
        # Ensure that backup is made of current config.
        logging.debug('Ensuring backup of current config.')
        current = Path("/etc/traffic-cop.yaml")
        utils.ensure_config_backup(current)

        # Update fallback config file.
        app.app.fallback_config = app.app.get_config_files()[0]

        target = worker.handle_button_config_clicked
        t_config = threading.Thread(target=target, name='T-cfg')
        t_config.start()
        # Set apply button to "sensitive".
        app.app.button_apply.set_sensitive(True)

        #target = worker.handle_config_changed
        #t_restart = threading.Thread(target=target)
        #t_restart.start()

    def on_button_apply_clicked(self, button):
        # Update the config file variable.
        app.app.config_file = Path('/etc/traffic-cop.yaml')
        # Restart the service to apply updated configuration.
        app.app.restart_service()
        # Disable the button again.
        button.set_sensitive(False)

    def on_button_reset_clicked(self, button):
        current = Path("/etc/traffic-cop.yaml")
        default = app.app.default_config

        # Get user confirmation before resetting configuration.
        approved = app.app.get_user_confirmation()
        if not approved:
            return

        # First check if current config matches default config.
        diff = utils.check_diff(current, default)
        if diff == 0:
            # Already using the default config.
            logging.debug("Using default config.")
            return

        # Ensure that backup is made of current config.
        logging.debug('Ensuring backup of current config.')
        utils.ensure_config_backup(current)

        # Copy /usr/share/traffic-cop/traffic-cop.yaml.default to /etc/traffic-cop.yaml;
        #   overwrite existing file.
        logging.debug('Setting config file to default.')
        shutil.copyfile(default, current)
        # Restart the service to apply default configuration.
        app.app.restart_service()
