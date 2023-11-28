""" Functions that run in background threads. """
# All of these functions run inside of threads and use GLib to communicate back.

import gi
import logging
import psutil
import subprocess
import sys
import time

from gi.repository import GLib
from pathlib import Path

# from trafficcop import app
# from trafficcop import rates
# from trafficcop import utils
from . import app
from . import rates
from . import utils


def handle_button_log_clicked():
    # Follow the log since service start time in a terminal window.
    cmd = [
        "gnome-terminal",
        "--",
        "journalctl",
        "--unit=traffic-cop.service",
        "--follow",
        "--output=cat",
        "--no-pager",
        "--since=\'" + app.app.svc_start_time + "\'",
    ]
    if app.app.svc_start_time == 'unknown':
        # Likely due to a kernel/systemd incompatibility.
        cmd.pop() # to remove the "--since" option
    cmd_txt = " ".join(cmd)
    # result = subprocess.run(cmd_txt, shell=True)
    result = subprocess.run(cmd)
    logging.debug(f"command: '{' '.join(cmd)}'; exit status: {result.returncode}")
    return

def handle_button_config_clicked():
    # Open config file in text editor.
    cmd = ["env", "SUDO_EDITOR=/usr/bin/gnome-text-editor", "sudoedit", "/etc/traffic-cop.yaml"]
    result = subprocess.run(cmd)
    logging.debug(f"command: '{' '.join(cmd)}'; exit status: {result.returncode}")

def handle_config_changed():
    pass
    #app.app.update_service_props()
    #read_time = app.app.tt_start
    #print("Service Start Time =", read_time)

def parse_nethogs_to_queue(queue, main_window):
    delay = 1
    device = utils.get_net_device()
    # If no device is given, then all devices are monitored, which double-counts
    #   on gateway device plus tc device.
    cmd = ['nethogs', '-t', '-v2', '-d' + str(delay), device]
    udp_support = utils.nethogs_supports_udp(utils.get_nethogs_version())
    logging.debug(f"{udp_support=}")
    if udp_support:
        cmd.insert(1, '-C')
    logging.debug(f"{cmd=}")
    stdout = subprocess.PIPE
    stderr = subprocess.STDOUT
    with subprocess.Popen(cmd, stdout=stdout, stderr=stderr, encoding='utf-8') as p:
        while main_window.is_visible():
            # There is a long wait for each line: sometimes nearly 2 seconds!
            line = p.stdout.readline().rstrip()
            if line == '':
                if p.poll() is None:
                    # Output line is blank; process still running.
                    continue
                else:
                    # Process completed (shouldn't happen).
                    break
            elif line.startswith('/') or line.startswith('unknown'):
                queue.put(line)

def bw_updater():
    while app.app.window.is_visible():
        time.sleep(1.5)
        # Update the device name.
        GLib.idle_add(app.app.update_device_name)

        # Get all applicable cmdlines & bytes transferred for each scope in config.
        # Sum the total sent for each scope, as well as the total received and give it a timestamp.
        app.app.scopes = rates.update_scopes(app.app.scopes, app.app.net_hogs_q, app.app.config_store)
        logging.debug(f"Current GUI scopes: {app.app.scopes}")

        # Get the upload and download rates (B/s).
        rates_dict = {}
        for scope, data in app.app.scopes.items():
            logging.debug(f"{scope=}")
            logging.debug(f"{data=}")
            # if not data['last']['time']:
            if None in data.get('last').values() or None in data.get('now').values():
                continue
            
            data_rates = rates.calculate_data_rates(data)
            if None in data_rates:
                continue

            # Adjust the number to only show 3 digits; change units as necessary (KB/s, MB/s, GB/s).
            human_up = utils.convert_bytes_to_human(data_rates[0])
            human_dn = utils.convert_bytes_to_human(data_rates[1])
            rates_dict[scope] = [*human_up, *human_dn]
        logging.debug(f"{rates_dict=}")
        if len(rates_dict) == 0:
            continue

        # Update the values shown in the treeview.
        GLib.idle_add(rates.update_store_rates, app.app.config_store, rates_dict)
