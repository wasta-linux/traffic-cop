""" Functions that run in background threads. """
# All of these functions run inside of threads and use GLib to communicate.

import logging
import subprocess
import time

from gi.repository import GLib

from . import rates
from . import utils


def parse_nethogs_to_queue(queue):
    delay = 1
    device = utils.get_net_device()
    # If no device is given, then all devices are monitored, which double-
    # counts on gateway device plus tc device.
    cmd = ['pkexec', 'nethogs', '-t', '-v2', '-d' + str(delay), device]
    udp_support = utils.nethogs_supports_udp(utils.get_nethogs_version())
    logging.debug(f"{udp_support=}")
    if udp_support:
        cmd.insert(2, '-C')
    logging.debug(f"{cmd=}")
    stdout = subprocess.PIPE
    stderr = subprocess.STDOUT
    with subprocess.Popen(
        cmd,
        stdout=stdout,
        stderr=stderr,
        encoding='utf-8'
    ) as p:
        while p.poll() is None:
            # There is a long wait for each line: sometimes nearly 2 seconds!
            line = p.stdout.readline().rstrip()
            if line.startswith('/') or line.startswith('unknown'):
                queue.put(line)


def bw_updater(app):
    while app.window.is_visible():
        time.sleep(1.5)
        # Update the device name.
        GLib.idle_add(app.update_device_name)

        # Get all applicable cmdlines & bytes transferred for each scope in
        # config. Sum the total sent for each scope, as well as the total
        # received and give it a timestamp.
        app.scopes = rates.update_scopes(
            app.scopes,
            app.net_hogs_q,
            app.config_store
        )
        logging.debug(f"Current GUI scopes: {app.scopes}")

        # Get the upload and download rates (B/s).
        rates_dict = {}
        for scope, data in app.scopes.items():
            logging.debug(f"{scope=}")
            logging.debug(f"{data=}")
            # if not data['last']['time']:
            if (
                None in data.get('last').values() or
                None in data.get('now').values()
            ):
                continue

            data_rates = rates.calculate_data_rates(data)
            if None in data_rates:
                continue

            # Adjust the number to only show 3 digits; change units as
            # necessary (KB/s, MB/s, GB/s).
            human_up = utils.convert_bytes_to_human(data_rates[0])
            human_dn = utils.convert_bytes_to_human(data_rates[1])
            rates_dict[scope] = [*human_up, *human_dn]
        logging.debug(f"{rates_dict=}")
        if len(rates_dict) == 0:
            continue

        # Update the values shown in the treeview.
        GLib.idle_add(rates.update_store_rates, app.config_store, rates_dict)
