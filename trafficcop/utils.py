import contextlib
import locale
import logging
import netifaces
import os
import psutil
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

    return device

def get_nethogs_version():
    cmd = ['nethogs', '-V']
    stdout = subprocess.PIPE
    stderr = subprocess.STDOUT
    r = subprocess.run(cmd, stdout=stdout, stderr=stderr, encoding='utf-8')
    version = r.stdout.split()[1]
    logging.info(f"nethogs version: {version}")
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
                s = time.strptime(human, '%a %b %d %H:%M:%S %Y') # Tue Oct 13 05:59:00 2020
                # Convert object to epoch format.
                epoch = time.mktime(s) # Tue 2020-10-13 05:59:00 WAT
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
        s = time.strptime(human, '%a %b %d %H:%M:%S %Y') # Tue Oct 13 05:59:00 2020
        # Convert object to log format.
        log = time.strftime('%a %Y-%m-%d %H:%M%S %Z', s) # Tue 2020-10-13 05:59:00 WAT
        return log

def convert_bytes_to_human(bytes_per_sec):
    logging.debug(f"{bytes_per_sec=}")
    # "human" means "3 significant digits, changing power as necessary."
    rate = bytes_per_sec
    unit = 'B/s'
    if len(str(int(rate))) > 3:
        # Switch to KB.
        rate = rate / 1000
        unit = 'KB/s'
        if len(str(int(rate))) > 3:
            # Switch to MB.
            rate = rate / 1000
            unit = 'MB/s'
            if len(str(int(rate))) > 3:
                # Switch to GB.
                rate = rate / 1000
                unit = 'GB/s'
    return [rate, unit]

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

def get_file_mtime(f):
    statinfo = os.stat(f)
    mtime = convert_epoch_to_human(statinfo.st_mtime)
    logging.debug(f"{f} last modified: {mtime}")
    return mtime

def wait_for_tt_start(exe='/usr/bin/tt', maxct=100):
    '''
    Wait for service status to start, otherwise update_service_props() may not
    get the correct info.
    '''
    ct = 0
    # Initially assume that tt is not running.
    tt_pid, tt_start, tt_dev = -1, '', ''
    while ct < maxct:
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
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=handlers,
    )

    # Print is better than logging for quick comprehension.
    print(f'traffic-cop log: {log_file_path}')
