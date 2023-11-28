""" Functions used to update bandwidth rates in GUI. """

import logging
import psutil
import re
import time

# from trafficcop import utils
from . import utils

def update_global_scope():
    '''
    Update system bandwidth usage for the Global scope.
    '''
    net_io = psutil.net_io_counters(pernic=True)
    dev = utils.get_net_device()
    if not dev:
        # No current connection.
        return [0, 0]
    bytes_up = net_io[dev].bytes_sent
    bytes_dn = net_io[dev].bytes_recv
    return [bytes_up, bytes_dn]

def update_scopes(scopes, queue, store):
    '''
    Retrieve items from nethogs queue and show updated download and upload rates.
    '''
    # Get time of current iteration.
    epoch = time.time()

    # Move current scopes dict's 'new' entries to 'last'.
    for scope in scopes.keys():
        scopes[scope]['last'] = scopes[scope]['now'].copy()

    # Get fresh proc list.
    proc_list = psutil.process_iter(attrs=['name', 'exe', 'cmdline'])

    # Update scopes dict 'new' entries.
    while not queue.empty():
        line = queue.get().split()
        logging.debug(f"nethogs line: {line}")
        if line[0] == 'unknown':
            exe_pid_usr = line[1]
        else:
            exe_pid_usr = line[0]
        b_up = int(float(line[-2]))
        b_dn = int(float(line[-1]))
        if b_up == 0 and b_dn == 0:
            # No traffic to track.
            logging.debug(f"Not updating GUI for 0-byte traffic.")
            continue
        scope = match_cmdline_to_scope(exe_pid_usr, store, proc_list)
        if not scope:
            # Not matched; will be counted in 'Global'.
            continue
        if scope not in scopes.keys():
            # Initialize scopes[scope].
            scopes[scope] = {
                'last': {
                    'time': None,
                    'bytes_up': None,
                    'bytes_dn': None,
                },
                'now': {}
            }

        # Add epoch time to 'now'.
        scopes[scope]['now']['time'] = epoch
        # Update bytes for current scope.
        scopes[scope]['now']['bytes_up'] = b_up
        scopes[scope]['now']['bytes_dn'] = b_dn

    # Update Global scope.
    b_up, b_dn = update_global_scope()
    if 'Global' not in scopes.keys():
        scopes['Global'] = {
                'last': {
                    'time': None,
                    'bytes_up': None,
                    'bytes_dn': None,
                },
                'now': {}
        }
    scopes['Global']['now']['time'] = epoch
    scopes['Global']['now']['bytes_up'] = b_up
    scopes['Global']['now']['bytes_dn'] = b_dn

    logging.debug(f"Updated data: {scopes}")
    return scopes

def get_proc_info_from_pid(pid, proc_list):
    # Match pid to process_iter.
    proc_info = {}
    for p in proc_list:
        try:
            p_pid = str(p.pid)
        except psutil.NoSuchProcess:
            continue
        if p_pid == pid:
            proc_info = p.info
            break
    logging.debug(f"Process info for pid {pid}: {proc_info}")
    return proc_info

def get_configured_scopes(store):
    # Get scope names, match-type, and match-str from store.
    scopes = {}
    for row in store:
        if row[0] == 'Global' or row[0] == 'unknown TCP' or row[0] == 'unknown UDP':
            continue
        scopes[row[0]] = row[11:]
    logging.debug(f"Configured scopes: {scopes}")
    return scopes

def match_proc_to_scope(proc, scopes):
    scope = None
    for s, data in scopes.items():
        if data[0] == 'name' or data[0] == 'cmdline':
            # See if scope 'name' or 'cmdline' matches proc 'name' or 'cmdline'.
            target_string = proc[data[0]]
            if data[0] == 'cmdline':
                # cmdline property from psutil given as list instead of string.
                target_string = ' '.join(proc[data[0]])
            logging.debug(f"Checking if \"{data[1]}\" matches \"{target_string}\"")
            match = re.match(data[1], target_string)
            if match:
                scope = s
                break
        elif data[0] == 'exe':
            # See if scope exe equals proc exe.
            logging.debug(f"Checking if \"{data[1]}\" = \"{proc[data[0]]}\"")
            if data[1] == proc[data[0]]:
                scope = s
                break
        else:
            # Unhandled match-type.
            logging.warning(f"Unhandled match-type for scope: '{s}: {data}'")
            continue
    logging.debug(f"Process \"{proc}\" matched to scope \"{scope}\"")
    return scope

def match_cmdline_to_scope(exe_pid_usr, store, proc_list):
    """
    Return Traffic Cop scope that matches pid of each line of nethogs output.
    This scope is used for displaying each process' traffic in the Traffic Cop window.
    Match (scope) options:
    - None (default)
    - unknown TCP
    - unknown UDP
    - other configured processes
    """
    logging.debug(f"Attempting to match traffic from: {exe_pid_usr}")
    # Strip pid and user from cmdline.
    cmdline_list = exe_pid_usr.split('/')
    # "exe" can be the path to an executable or "TCP"/"UDP".
    exe = '/'.join(cmdline_list[:-2])
    logging.debug(f"{exe=}")
    # "pid" will be "0" if "exe" is "TCP" or "UDP".
    pid = cmdline_list[-2]
    logging.debug(f"{pid=}")

    scope = None
    if exe == 'TCP':
        scope = 'unknown TCP'
    elif exe == 'UDP':
        scope = 'unknown UDP'
    else:
        # Match pid to process_iter.
        matched_proc = get_proc_info_from_pid(pid, proc_list)
        if matched_proc:
            # Get scope names, match-type, and match-str from store.
            scopes = get_configured_scopes(store)
            if scopes:
                scope = match_proc_to_scope(matched_proc, scopes)

    logging.debug(f"nethogs line \"{exe_pid_usr}\" matched to scope \"{scope}\"")
    return scope

def update_store_rates(store, rates_dict):
    # WARNING: This assumes the scopes in rates_dict are the same as those in store.
    #   In other words it assumes that the store hasn't changed.
    logging.debug(f"New bandwidth rates for GUI: {rates_dict}")
    for row in store:
        for scope, values in rates_dict.items():
            if row[0] == scope:
                logging.debug(f"{values=}")
                if values[0] <= 0:
                    row[7] = ' '*4
                    row[8] = ' '*4
                else:
                    # row[7] = '{:.0f}'.format(values[0])
                    row[7] = f"{values[0]:.0f}"
                    row[8] = values[1]
                if values[2] <= 0:
                    row[9] = ' '*4
                    row[10] = ' '*4
                else:
                    # row[9] = '{:.0f}'.format(values[2])
                    row[9] = f"{values[2]:.0f}"
                    row[10] = values[3]
                break

def calculate_data_rates(data):
    t1 = data['now']['time']
    t0 = data['last']['time']
    u1 = data['now']['bytes_up']
    u0 = data['last']['bytes_up']
    d1 = data['now']['bytes_dn']
    d0 = data['last']['bytes_dn']
    logging.debug(f"{t0=}; {t1=}")
    logging.debug(f"{u0=}; {u1=}")
    logging.debug(f"{d0=}; {d1=}")
    elapsed = t1 - t0
    bytes_up = u1 - u0
    bytes_dn = d1 - d0
    rate_up = 0
    rate_dn = 0
    if elapsed > 0:
        rate_up = bytes_up / elapsed
        rate_dn = bytes_dn / elapsed
    return [rate_dn, rate_up]
