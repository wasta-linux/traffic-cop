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


def print_result(cmd, result):
    print(f"'{' '.join(cmd)}' -> {result.returncode}")


def get_net_device():
    '''
    Return the gateway internet device.
    If there are multiple gateways found, then the highest priority device is
    returned:
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
    base_version = version_string.split('-')[0]
    if version.parse(base_version) >= version.parse('0.8.6'):
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
                # Ex: Tue Oct 13 05:59:00 2020
                s = time.strptime(human, '%a %b %d %H:%M:%S %Y')
                # Convert object to epoch format.
                epoch = time.mktime(s)  # Tue 2020-10-13 05:59:00 WAT
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
        # Ex: Tue Oct 13 05:59:00 2020
        s = time.strptime(human, '%a %b %d %H:%M:%S %Y')
        # Convert object to log format; ex: Tue 2020-10-13 05:59:00 WAT
        log = time.strftime('%a %Y-%m-%d %H:%M%S %Z', s)
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


def get_systemd_service_props():
    unit_file_state = 'unknown'
    active_state = 'unknown'
    svc_start_time = 'unknown'

    cmd = [
        "systemctl",
        "show",
        "traffic-cop.service",
        "--no-pager",
    ]
    p = subprocess.run(cmd, capture_output=True, encoding='UTF8')
    if p.returncode != 0:
        # Status output error. Probably due to kernel incompatibility after
        # update. Fall back to trying "systemctl status" command instead.
        cmd[1] = "status"
        p_status = subprocess.run(cmd, capture_output=True, encoding='UTF8')
        output_list = p_status.stdout.splitlines()
        upat = r'\s+Loaded: loaded \(/etc/systemd/system/traffic-cop.service; (.*);.*'  # noqa: E501
        apat = r'\s+Active: (.*) since .*'
        for line in output_list:
            try:
                match = re.match(upat, line)
                unit_file_state = match.group(1)
            except AttributeError:
                pass
            try:
                match = re.match(apat, line)
                active_state = match.group(1).split()[0]
            except AttributeError:
                pass

    # Continue with processing of "systemctl show" command output.
    for line in p.stdout.splitlines():
        if line.startswith('UnitFileState='):
            unit_file_state = line.split('=')[1]
        elif line.startswith('ActiveState='):
            active_state = line.split('=')[1]
        elif line.startswith('ExecMainStartTimestamp='):
            svc_start_time = line.split('=')[1]

    return (unit_file_state, active_state, svc_start_time)


def get_tt_info(exe='/usr/bin/tt'):
    procs = psutil.process_iter(attrs=['pid', 'cmdline', 'create_time'])
    for proc in procs:
        try:
            if exe == proc.cmdline()[1]:
                proc.dev = proc.cmdline()[2]
                proc.start = convert_epoch_to_human(proc.create_time())
                return proc.pid, proc.start, proc.dev
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
            IndexError
        ):
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
        msg = f"Copying {default_config_file} to {runtime_config_file}."
        logging.debug(msg)
        # Running as separate process so that pkexec can be invoked. However,
        # it seems that having another traffic-cop process running at the same
        # time leads to this error: "Failed to register: Timeout was reached"
        # So we need to exit the current process while also launching the reset
        # process, then re-launching the GUI.
        subprocess.Popen(['pkexec', '/usr/bin/traffic-cop', '--reset'])
        return True


def reset_config_file(default, active):
    # NOTE: This runs with elevated privileges.
    return shutil.copyfile(default, active)


def run_command(command_tokens):
    p = subprocess.run(command_tokens, capture_output=True, encoding='UTF8')
    logging.debug(p.stdout)
    if p.returncode != 0:
        logging.error(p.stderr)
    return p.returncode


def set_up_logging(log_level):
    # Define log file.
    # log_dir = Path('/var', 'log', 'traffic-cop')
    log_dir = Path('~', '.traffic-cop').expanduser()
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
