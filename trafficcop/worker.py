""" Functions that run in background threads. """
# All of these functions run inside of threads and use GLib to communicate back.

import gi
import psutil
import subprocess
import sys
import time

from gi.repository import GLib
from pathlib import Path

from trafficcop import app
from trafficcop import config
from trafficcop import utils


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
    result = subprocess.run(cmd_txt, shell=True)
    print(f"{cmd} -> {result.returncode}")
    return

def handle_button_config_clicked():
    # Open config file in gedit.
    cmd = ["gedit", "/etc/traffic-cop.conf"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    utils.print_result(cmd, result)

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
    if udp_support:
        cmd.insert(1, '-C')
    stdout = subprocess.PIPE
    stderr = subprocess.STDOUT
    with subprocess.Popen(cmd, stdout=stdout, stderr=stderr, encoding='utf-8') as p:
        while main_window.is_visible():
            # There is a long wait for each line: sometimes nearly 2 seconds!
            line = p.stdout.readline().rstrip()
            if line == '':
                if p.poll() is None:
                    # Output line is blank.
                    continue
                else:
                    # Process completed. (Shouldn't happen.)
                    break
            elif line[0] == '/' or line.split()[0] == 'unknown':
                queue.put(line)

def bw_updater():
    while app.app.window.is_visible():
        time.sleep(1.5)
        # Update the device name.
        GLib.idle_add(app.app.update_device_name)

        # Get all applicable cmdlines & bytes transferred for each scope in config.
        # Sum the total sent for each scope, as well as the total received and give it a timestamp.
        app.app.scopes = utils.update_scopes(app.app.scopes, app.app.net_hogs_q, app.app.config_store)

        # Get the upload and download rates (B/s).
        rates_dict = {}
        for scope, data in app.app.scopes.items():
            if not data['last']['time']:
                continue
            rates = utils.calculate_data_rates(data)

            # Adjust the number to only show 3 digits; change units as necessary (KB/s, MB/s, GB/s).
            human_up = utils.convert_bytes_to_human(rates[0])
            human_dn = utils.convert_bytes_to_human(rates[1])
            rates_dict[scope] = [*human_up, *human_dn]

        # Update the values shown in the treeview.
        GLib.idle_add(config.update_store_rates, app.app.config_store, rates_dict)
