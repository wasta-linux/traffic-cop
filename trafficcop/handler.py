""" Signal handler module. """

import logging
import subprocess
import threading
from pathlib import Path

from . import config
from . import utils


class Handler():
    def __init__(self, app):
        self.app = app

    def gtk_widget_destroy(self, *args):
        self.app.quit()

    def on_toggle_unit_state_state_set(self, widget, state):
        # Apply new state to the service.
        if state == True:
            cmd = ["systemctl", "enable", "traffic-cop.service"]
        elif state == False:
            cmd = ["systemctl", "disable", "traffic-cop.service"]
        p = subprocess.run(cmd)
        # Ensure that toggle button matches true state.
        self.app.update_state_toggles()

    def on_toggle_active_state_set(self, widget, state):
        # Apply new state to the service.
        current_status = utils.get_systemd_service_props()[1]
        if state == True and current_status != 'active':
            if not self.app.start_service():
                self.app.toggle_active.set_state(False)
        elif state == False and current_status != 'inactive':
            if not self.app.stop_service():
                self.app.toggle_active.set_state(True)

    def on_button_restart_clicked(self, folder_obj):
        self.app.restart_service()

    def on_button_log_clicked(self, *args):
        # Follow the log since service start time in a terminal window.
        cmd = [
            "gnome-terminal",
            "--",
            "journalctl",
            "--unit=traffic-cop.service",
            "--follow",
            "--output=cat",
            "--no-pager",
            "--since=\'" + app.svc_start_time + "\'",
        ]
        if app.svc_start_time == 'unknown':
            cmd.pop() # remove the "--since" option
        cmd_txt = " ".join(cmd)
        subprocess.Popen(cmd_txt, shell=True) # terminal closes immediately without shell=True

    def on_button_config_clicked(self, *args):
        # NOTE: Button later renamed to "Edit..."
        subprocess.Popen(["/usr/bin/gnome-text-editor", "admin:///etc/traffic-cop.yaml"])
        # Set apply button to "sensitive".
        self.app.button_apply.set_sensitive(True)

    def on_button_apply_clicked(self, button):
        # Check service status and update widgets.
        if self.app.active_state == 'active':
            # Restart the service to apply updated configuration.
            self.app.restart_service()
        else:
            self.app.config_store = config.convert_yaml_to_store(self.app.config_file)
            # self.app.update_info_widgets()
            self.app.treeview_config = self.app.update_treeview_config()
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

        # Copy /usr/share/traffic-cop/traffic-cop.yaml.default to /etc/traffic-cop.yaml;
        #   overwrite existing file.
        logging.debug('Setting config file to default.')
        rc = utils.run_command(['/usr/bin/traffic-cop', '--reset'])

        # Update window and restart service, if active.
        self.on_button_apply_clicked(self.app.button_apply)
