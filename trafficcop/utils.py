import contextlib
import locale
import logging
import netifaces
import os
import psutil
import re
import shutil
import subprocess
import sys
import time

from packaging import version
from pathlib import Path


@contextlib.contextmanager
def setlocale(*args, **kw):
    saved = locale.setlocale(locale.LC_ALL)
    yield locale.setlocale(*args, **kw)
    locale.setlocale(locale.LC_ALL, saved)

def mute(func, *args, **kwargs):
    with open(os.devnull, 'w') as devnull:
        with contextlib.redirect_stdout(devnull):
            output = func(*args, **kwargs)
    return output

def print_result(cmd, result):
    print(f"'{' '.join(cmd)}' -> {result.returncode}")

def get_net_device():
    '''
    Return the gateway internet device.
    If there are multiple gateways found, then the highest priority device is returned:
        1. Bluetooth (AF_BLUETOOTH)
        2. Cellular (AF_PPPOX)
        3. IPv6 (AF_INET6)
        4. IPv4 (AF_INET)
    '''
    gws = netifaces.gateways()
    families = [
        netifaces.AF_BLUETOOTH,
        netifaces.AF_PPPOX,
        netifaces.AF_INET6,
        netifaces.AF_INET,
    ]
    for family in families:
        try:
            device = gws['default'][family][1]
            break
        except KeyError:
            device = None
    logging.debug(f"Network device: {device}")
    return device

def get_nethogs_version():
    cmd = ['nethogs', '-V']
    stdout = subprocess.PIPE
    stderr = subprocess.STDOUT
    r = subprocess.run(cmd, stdout=stdout, stderr=stderr, encoding='utf-8')
    version = r.stdout.split()[1]
    logging.debug(f"nethogs version: {version}")
    return version

def nethogs_supports_udp(version_string):
    if version.parse(version_string) >= version.parse('0.8.6'):
        return True
    else:
        return False

def convert_epoch_to_human(epoch):
    human = time.ctime(epoch)
    return human

def convert_human_to_epoch(human):
    if human:
        with setlocale(locale.LC_TIME, "C"):
            try:
                str = time.strptime(human, '%a %b %d %H:%M:%S %Y') # Tue Oct 13 05:59:00 2020
                # Convert object to epoch format.
                epoch = time.mktime(str) # Tue 2020-10-13 05:59:00 WAT
            except ValueError as v:
                logging.debug(repr(v))
                epoch = ''
            except Exception as e:
                logging.debug(repr(e))
                epoch = ''
    else:
        epoch = human
    return epoch

def convert_human_to_log(human):
    # Convert human to object.
    #   Doesn't work: prob b/c my locale is FR but human is reported in EN.
    with setlocale(locale.LC_TIME, "C"):
        str = time.strptime(human, '%a %b %d %H:%M:%S %Y') # Tue Oct 13 05:59:00 2020
        # Convert object to log format.
        log = time.strftime('%a %Y-%m-%d %H:%M%S %Z', str) # Tue 2020-10-13 05:59:00 WAT
        return log

def convert_bytes_to_human(bytes_per_sec):
    # "human" means "3 significant digits, changing power as necessary."
    float = bytes_per_sec
    unit = 'B/s'
    if len(str(int(float))) > 3:
        # Switch to KB.
        float = float / 1000
        unit = 'KB/s'
        if len(str(int(float))) > 3:
            # Switch to MB.
            float = float / 1000
            unit = 'MB/s'
            if len(str(int(float))) > 3:
                # Switch to GB.
                float = float / 1000
                unit = 'GB/s'
    return [float, unit]

def get_tt_info(exe='/usr/bin/tt'):
    procs = psutil.process_iter(attrs=['pid', 'cmdline', 'create_time'])
    for proc in procs:
        try:
            if exe == proc.cmdline()[1]:
                proc.dev = proc.cmdline()[2]
                proc.start = convert_epoch_to_human(proc.create_time())
                return proc.pid, proc.start, proc.dev
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, IndexError):
            pass
    return -1, '', ''

def get_file_mtime(file):
    statinfo = os.stat(file)
    mtime = convert_epoch_to_human(statinfo.st_mtime)
    logging.debug(f"{file} last modified: {mtime}")
    return mtime

def wait_for_tt_start(exe='/usr/bin/tt', max=100):
    '''
    Wait for service status to start, otherwise update_service_props() may not
    get the correct info.
    '''
    ct = 0
    # Initially assume that tt is not running.
    tt_pid, tt_start, tt_dev = -1, '', ''
    while ct < max:
        tt_pid, tt_start, tt_dev = get_tt_info(exe)
        if psutil.pid_exists(tt_pid):
            return tt_pid, tt_start, tt_dev
        time.sleep(0.1)
        ct += 1
    return tt_pid, tt_start, tt_dev

def check_diff(file1, file2):
    result = subprocess.run(
        ["diff", file1, file2],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return result.returncode

def ensure_config_file(default_config_file, runtime_config_file):
    if not runtime_config_file.is_file():
        logging.debug(f"Copying {default_config_file} to {runtime_config_file}.")
        shutil.copyfile(default_config_file, runtime_config_file)

def ensure_config_backup(current):
    '''
    Make a backup of user config; add index to file name if other backup already exists.
    '''
    already = "Current config already backed up at"
    name = current.stem
    suffix = ".yaml.bak"
    backup = current.with_suffix(suffix)
    if not backup.exists():
        shutil.copyfile(current, backup)
        return True
    diff = check_diff(current, backup)
    if diff == 0:
        logging.debug(already, backup)
        return True
    # The backup file exists and is different from current config:
    #   need to choose new backup file name and check again.
    # Add index to name.
    i = 1
    # Set new backup file name.
    backup = current.with_name(name + '-' + str(i)).with_suffix(suffix)
    if not backup.exists():
        shutil.copyfile(current, backup)
        return True
    diff = check_diff(current, backup)
    if diff == 0:
        logging.debug(already, backup)
        return True
    while backup.exists():
        # Keep trying new indices until an available one is found.
        i += 1
        backup = current.with_name(name + '-' + str(i)).with_suffix(suffix)
        if not backup.exists():
            shutil.copyfile(current, backup)
            return True
        diff = check_diff(current, backup)
        if diff == 0:
            logging.debug(already, backup)
            return True

def update_global_scope():
    '''
    Update system bandwidth usage for the Global scope.
    '''
    net_io = psutil.net_io_counters(pernic=True)
    dev = get_net_device()
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
        if line[0] == 'unknown':
            exe_pid_usr = line[1]
        else:
            exe_pid_usr = line[0]
        b_up = int(float(line[-2]))
        b_dn = int(float(line[-1]))
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
    bytes = update_global_scope()
    b_up = bytes[0]
    b_dn = bytes[1]
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

    logging.debug(f"Store data: {scopes}")
    return scopes

def match_cmdline_to_scope(exe_pid_usr, store, proc_list):
    # Strip pid and user from cmdline.
    cmdline_list = exe_pid_usr.split('/')
    # "exe" can be the path to an executable or "TCP"/"UDP".
    exe = '/'.join(cmdline_list[:-2])
    # "pid" will be "0" if "exe" is "TCP" or "UDP".
    pid = cmdline_list[-2]

    # Get scope names, match-type, and match-str from store.
    scopes = {}
    for row in store:
        if row[0] == 'Global':
            continue
        scopes[row[0]] = row[11:]

    # Get cmdlines from proces_iter.
    match_exe_pid_usr_and_proc = {}
    for proc in proc_list:
        try:
            p_pid = str(proc.pid)
        except psutil.NoSuchProcess:
            continue
        if p_pid == pid:
            match_exe_pid_usr_and_proc = proc.info
            break

    # Match cmdline with scope.
    scope = None
    if exe == 'TCP':
        scope = 'unknown TCP'
    elif exe == 'UDP':
        scope = 'unknown UDP'
    elif match_exe_pid_usr_and_proc:
        for k, v in scopes.items():
            # k = scope; v = [match-type, match-str]
            if v[0] == 'name':
                match = re.match(v[1], match_exe_pid_usr_and_proc['name'])
                if match:
                    scope = k
                    break
            elif v[0] == 'exe':
                # See if scope exe matches proc exe.
                match = re.match(v[1], match_exe_pid_usr_and_proc['exe'])
                if match:
                    scope = k
                    break
            elif v[0] == 'cmdline':
                # See if scope cmdline equals proc cmdline.
                if v[1] == match_exe_pid_usr_and_proc['cmdline']:
                    scope = k
                    break
            else:
                # Unhandled match-type.
                print(f"no match for: '{k}: {v}'")
                continue
    logging.debug(f"\"{exe_pid_usr}\" matched to \"{scope}\"")
    return scope

def calculate_data_rates(data):
    elapsed = data['now']['time'] - data['last']['time']
    bytes_up = data['now']['bytes_up'] - data['last']['bytes_up']
    bytes_dn = data['now']['bytes_dn'] - data['last']['bytes_dn']
    rate_up = 0
    rate_dn = 0
    if elapsed > 0:
        rate_up = bytes_up / elapsed
        rate_dn = bytes_dn / elapsed
    return [rate_dn, rate_up]

def set_up_logging(log_level):
    # Define log file.
    log_dir = Path('/var', 'log', 'traffic-cop')
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = log_dir / 'traffic-cop.log'

    # Define logging handlers.
    file_h = logging.FileHandler(log_file_path)
    file_h.setLevel(logging.INFO)
    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setLevel(logging.WARNING)
    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_level = logging.ERROR
    if log_level == logging.DEBUG:
        stderr_level = log_level
    stderr_h.setLevel(stderr_level)
    handlers = [file_h, stdout_h, stderr_h]

    # Set initial config.
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%H:%M:%S',
        handlers=handlers
    )

    # Print is better than logging for quick comprehension.
    print(f'traffic-cop log: {log_file_path}')
