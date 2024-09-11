""" Main GUI module. """

import gi
import logging
import os
import queue
import subprocess
import sys
import threading

from pathlib import Path
current_file_path = Path(__file__)

gi.require_version("Gtk", "3.0")
from gi.repository import Gio       # noqa: E402
from gi.repository import GLib      # noqa: E402
from gi.repository import Gtk       # noqa: E402

from . import config                # noqa: E402
from . import handler               # noqa: E402
from . import utils                 # noqa: E402
from . import worker                # noqa: E402


class TrafficCop(Gtk.Application):
    # Ref:
    # https://python-gtk-3-tutorial.readthedocs.io/en/latest/application.html
    def __init__(self):
        super().__init__(
            application_id='org.wasta.apps.traffic-cop',
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        # Add CLI options.
        self.add_main_option(
            'version', ord('V'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            'Print version number', None
        )
        self.add_main_option(
            'debug', ord('d'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            'Print DEBUG info to stdout', None
        )
        self.add_main_option(
            'reset', ord('r'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            'Reset config file to default.', None
        )

        # Get UI location based on current file location.
        self.ui_dir = '/usr/share/traffic-cop/ui'
        pkgs = '/usr/lib/python3/dist-packages'
        if str(current_file_path.parents[1]) != pkgs:
            self.ui_dir = str(current_file_path.parents[1] / 'data' / 'ui')

        # Define app-wide variables.
        self.app_pid = os.getpid()
        self.tt_pid, self.tt_start, self.tt_dev = utils.get_tt_info()
        props = utils.get_systemd_service_props()
        self.unit_file_state, self.active_state, self.svc_start_time = props
        self.config_file = Path('/etc/traffic-cop.yaml')
        cfg = Path("/usr/share/traffic-cop/traffic-cop.yaml.default")
        self.default_config = cfg
        self.config_store = ''
        self.net_hogs_q = queue.Queue()
        self.main_pid = os.getpid()
        self.managed_ports = {}
        self.scopes = {}

    def do_startup(self):
        '''
        do_startup is the setting up of the app, either for "activate" or for
        "open". It runs just after __init__.
        '''
        # Set log level.
        self.log_level = logging.INFO

        # Define builder and its widgets.
        Gtk.Application.do_startup(self)

        # Get widgets from glade file, which is defined in __init__.
        self.builder = Gtk.Builder()
        self.builder.add_from_file(self.ui_dir + '/mainwindow.glade')
        self.window = self.builder.get_object('mainwindow')
        self.toggle_active = self.builder.get_object('toggle_active')
        self.toggle_unit_state = self.builder.get_object('toggle_unit_state')
        self.button_restart = self.builder.get_object('button_restart')
        self.label_iface = self.builder.get_object('label_iface')
        self.button_log = self.builder.get_object('button_log')
        self.label_applied = self.builder.get_object('label_applied')
        self.button_apply = self.builder.get_object('button_apply')
        self.button_config = self.builder.get_object('button_config')
        self.button_reset = self.builder.get_object('button_reset')
        self.w_config = self.builder.get_object('w_config')
        self.vp_config = self.builder.get_object('vp_config')

        # Main window.
        self.add_window(self.window)
        self.window.set_icon_name('traffic-cop')

        # # Populate config viewport.
        # self.treeview_config = self.update_treeview_config()
        # self.treeview_config.show()
        # self.vp_config.add(self.treeview_config)

        # Populate widget data.
        self.button_apply.set_sensitive(False)

        # Connect GUI signals to Handler class.
        self.builder.connect_signals(handler.Handler(self))

    def do_command_line(self, command_line):
        '''
        do_command_line runs after do_startup and before do_activate.
        '''
        rc = 0
        options = command_line.get_options_dict()
        self.options = options.end().unpack()

        if 'version' in self.options:
            print(f"traffic-cop {config.VERSION}")
            self.quit()
            sys.exit(rc)

        if 'debug' in self.options:
            self.log_level = logging.DEBUG

        # Start logging.
        utils.set_up_logging(self.log_level)
        logging.info("Traffic-Cop started.")
        logging.debug(f"CLI options: {self.options}")

        if 'reset' in self.options:
            # Ensure elevated privileges.
            if os.geteuid() != 0:
                rc = 1
                msg = "Please rerun the command with pkexec or sudo."
                logging.critial(msg)
                self.quit()
                sys.exit(rc)

            # Reset the config file.
            r = utils.reset_config_file(self.default_config, self.config_file)
            logging.debug(f"'reset' return value: '{r}'; type: '{type(r)}'")
            if isinstance(r, str) and len(r) > 0:
                # Success b/c shutil.filecopy returned dest path; launch GUI.
                subprocess.Popen(['/usr/bin/traffic-cop'])
            else:
                msg = f"Failed to reset config file: {self.config_file}"
                logging.critical(msg)
                rc = 1
            self.quit()
            sys.exit(rc)

        # Activate app.
        self.activate()
        return 0

    def do_activate(self):
        '''
        do_activate is the displaying of the window. It runs last after
        do_command_line.
        '''

        # # Start logging.
        # utils.set_up_logging(self.log_level)
        # logging.info("Traffic-Cop GUI started.")
        # logging.debug(f"CLI options: {self.options}")

        # Initialize variables.
        self.svc_start_time = 'unknown'

        # Ensure config file exists.
        if utils.ensure_config_file(self.default_config, self.config_file):
            self.quit()
            sys.exit(0)

        # Populate config viewport.
        self.treeview_config = self.update_treeview_config()
        self.treeview_config.show()
        self.vp_config.add(self.treeview_config)

        # Update widgets and show window.
        self.update_info_widgets()
        self.window.show()

        # Start tracking operations (self.window must be shown first).
        self.t_nethogs = threading.Thread(
            name='T-nh',
            target=worker.parse_nethogs_to_queue,
            args=(self.net_hogs_q,),
            daemon=True,
        )
        self.t_nethogs.start()

        # Start bandwidth rate updater.
        self.t_bw_updater = threading.Thread(
            name='T-bw',
            target=worker.bw_updater,
            args=(self,),
            daemon=True,
        )
        self.t_bw_updater.start()

    def update_service_props(self):
        # Get true service start time.
        self.tt_pid, self.tt_start, self.tt_dev = utils.get_tt_info()
        # Get state of systemd service.
        props = utils.get_systemd_service_props()
        self.unit_file_state, self.active_state, self.svc_start_time = props

    def update_state_toggles(self):
        # Update toggle buttons according to current states.
        state = True if self.unit_file_state == 'enabled' else False
        self.toggle_unit_state.set_state(state)
        state = True if self.active_state == 'active' else False
        self.toggle_active.set_state(state)

    def update_device_name(self):
        # Get name of managed interface.
        pid, time, dev = utils.get_tt_info()
        if pid == -1:
            dev = '--'
        if self.label_iface.get_text() != dev:
            logging.info(f"Managed device: {dev}")
            self.label_iface.set_text(dev)

    def update_config_time(self):
        self.label_applied.set_text(self.tt_start)
        # logging.debug(f"Updated config time: {self.tt_start}")

    def update_button_states(self):
        # TODO: I need a way to "watch" the config file if setting the "Apply"
        #   button to "sensitive" is ever going to work.
        # Update "Apply" button to be insensitive.
        # self.button_apply.set_sensitive(False)
        # Update "Reset..." button to be insensitive.
        self.button_reset.set_sensitive(False)

        # Set "Apply" button to proper state.
        # config_mtime = utils.get_file_mtime(self.config_file)
        # if self.tt_start and config_mtime > self.tt_start:
        #     Update "Apply" button to be sensitive.
        #     self.button_apply.set_sensitive(True)

        # Set "Reset..." button to proper state.
        diff_configs = utils.check_diff(self.config_file, self.default_config)
        if not diff_configs == 0:
            # Update "Reset..." button to be sensitive.
            self.button_reset.set_sensitive(True)

    def update_treeview_config(self):
        '''
        This handles both initial config display and updating the display if
        the config file is edited externally.
        '''
        if not self.config_store:
            # App is just starting up; create the store.
            self.config_store = config.convert_yaml_to_store(self.config_file)

        if self.tt_start:
            # Service is running.
            # Check if modified time of config file is newer than last service
            # restart.
            #   The config could have been externally modified. If so, those
            #   changes could be shown here in the app without them actually
            #   having been applied.
            config_mtime = utils.get_file_mtime(self.config_file)
            config_epoch = utils.convert_human_to_epoch(config_mtime)
            tt_epoch = utils.convert_human_to_epoch(self.tt_start)
            if config_epoch > tt_epoch:
                logging.warning(
                    "The config file has been modified since the service"
                    "started.\nApplying the changes now."
                )
                self.restart_service()
                return config.create_config_treeview(self.config_store)

        new_config_store = config.convert_yaml_to_store(self.config_file)
        self.config_store = config.update_config_store(
            self.config_store,
            new_config_store,
        )

        return config.create_config_treeview(self.config_store)

    def update_info_widgets(self):
        self.update_service_props()
        self.update_state_toggles()
        self.update_device_name()
        self.update_config_time()
        self.update_button_states()

    def stop_service(self):
        cmd = ["pkexec", "systemctl", "stop", "traffic-cop.service"]
        rc = utils.run_command(cmd)
        self.update_info_widgets()
        if rc != 0:
            return False
        else:
            return True

    def start_service(self):
        cmd = ["pkexec", "systemctl", "start", "traffic-cop.service"]
        rc = utils.run_command(cmd)
        if rc != 0:
            self.update_info_widgets()
            return False
        self.tt_pid, self.tt_start, self.tt_dev = utils.wait_for_tt_start()
        self.update_info_widgets()
        self.treeview_config = self.update_treeview_config()
        return True

    def restart_service(self):
        cmd = ["pkexec", "systemctl", "restart", "traffic-cop.service"]
        rc = utils.run_command(cmd)
        if rc != 0:
            self.update_info_widgets()
            return False
        self.tt_pid, self.tt_start, self.tt_dev = utils.wait_for_tt_start()
        # Check service status and update widgets.
        self.update_info_widgets()
        self.treeview_config = self.update_treeview_config()
        return True

    def get_user_confirmation(self):
        text = "The current configuration file will be overwritten."
        label = Gtk.Label(text)
        dialog = Gtk.Dialog(
            'Reset to default configuration?',
            self.window,
            None,  # Gtk.Dialog.DESTROY_WITH_PARENT,
            (
                Gtk.STOCK_CANCEL,
                Gtk.ResponseType.CANCEL,
                Gtk.STOCK_OK,
                Gtk.ResponseType.OK,
            )
        )
        hmarg = 80
        vmarg = 20
        dialog.vbox.set_margin_top(vmarg)
        dialog.vbox.set_margin_bottom(vmarg)
        dialog.vbox.set_margin_start(hmarg)
        dialog.vbox.set_margin_end(hmarg)
        dialog.vbox.set_spacing(20)
        dialog.vbox.pack_start(label, True, True, 5)
        label.show()
        response = dialog.run()
        # CLOSE: -4, OK: -5, CANCEL: -6
        dialog.destroy()
        if response == -5:
            return True
        else:
            return False


def main():
    app = TrafficCop()
    app.run(sys.argv)
