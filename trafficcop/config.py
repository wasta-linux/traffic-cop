import gi
import logging
import re
import schema
import subprocess
import yaml

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk       # noqa: E402
from pathlib import Path            # noqa: E402


VERSION = '1.2.12'


def create_config_treeview(store):
    tree = Gtk.TreeView(model=store)
    r_left = Gtk.CellRendererText()
    r_left.set_alignment(0.0, 0.0)
    r_center = Gtk.CellRendererText()
    r_center.set_alignment(0.5, 0.0)
    r_right = Gtk.CellRendererText()
    r_right.set_alignment(1.0, 0.0)

    # Configure columns.
    c_name = Gtk.TreeViewColumn("Process", r_left, text=0)
    c_name.set_sort_column_id(0)

    up = '\u2191'
    dn = '\u2193'
    c_dn_max = Gtk.TreeViewColumn(f"Max {dn}", r_right, text=1)
    c_up_max = Gtk.TreeViewColumn(f"Max {up}", r_right, text=2)
    c_dn_min = Gtk.TreeViewColumn(f"Min {dn}", r_right, text=3)
    c_up_min = Gtk.TreeViewColumn(f"Min {up}", r_right, text=4)
    rates = [c_dn_max, c_up_max, c_dn_min, c_up_min]
    for r in rates:
        r.set_fixed_width(80)

    c_dn_pri = Gtk.TreeViewColumn(f"Priority {dn}", r_center, text=5)
    c_dn_pri.set_sort_column_id(5)
    c_up_pri = Gtk.TreeViewColumn(f"Priority {up}", r_center, text=6)
    c_up_pri.set_sort_column_id(6)

    c_dn_rt = Gtk.TreeViewColumn(f"Rate {dn}", r_right, text=7)
    c_dn_u = Gtk.TreeViewColumn("", r_left, text=8)
    c_dn_u.set_fixed_width(40)

    c_up_rt = Gtk.TreeViewColumn(f"Rate {up}", r_right, text=9)
    c_up_u = Gtk.TreeViewColumn("", r_left, text=10)
    c_up_u.set_fixed_width(40)
    tree.append_column(c_name)
    c_list = [c_dn_max, c_up_max, c_dn_min, c_up_min, c_dn_pri, c_up_pri]
    for c in c_list:
        c.set_alignment(0.5)    # set title alignment
        c.set_expand(True)      # expand when window is wider than necessary
        tree.append_column(c)
    tree.append_column(c_dn_rt)
    tree.append_column(c_dn_u)
    tree.append_column(c_up_rt)
    tree.append_column(c_up_u)
    return tree


def update_config_store(store, new_store):
    # For each row in new_store:
    for nrow in new_store:
        # If new_store row is in old store...
        nscope = nrow[0]
        match = False
        for row in store:
            scope = row[0]
            if nscope == scope:
                match = True
                if nrow[1:7] != row[1:7]:
                    # ...update old store row with new_store row data.
                    row[1:7] = nrow[1:7]
                break
        # If new_store row is not in old store...
        if not match:
            # ...append new_store row to old store.
            store.append(nrow[:])
    # For each row in old store:
    for row in store:
        # If old store row is not in new_store...
        scope = row[0]
        match = False
        for nrow in new_store:
            nscope = nrow[0]
            if scope == nscope:
                match = True
        if not match:
            # ...remove old store row.
            store.remove(row.iter)
    return store


def convert_dict_to_list(name, v_dict):
    if not type(v_dict) is dict:
        # Scope (Global or Process) not given valid config.
        v_dict = {}

    # Set defaults.
    dn_max = v_dict.get('download', '')
    up_max = v_dict.get('upload', '')
    dn_min = v_dict.get('download-minimum', '')
    up_min = v_dict.get('upload-minimum', '')
    dn_pri = v_dict.get('download-priority', 9)
    up_pri = v_dict.get('upload-priority', 9)
    dn_rate = ' '*4  # '{:.2f}'.format(0)
    dn_unit = ' '*4  # 'B/s'
    up_rate = ' '*4  # '{:.2f}'.format(0)
    up_unit = ' '*4  # 'B/s'

    # The match section can theoretically be any of the attributes that
    #   psutil.process exposes. This could get really complicated. Just going
    #   to support 'name', 'exe', and 'cmdline'. Others seem less useful.
    # List of possible attributes:
    #   https://psutil.readthedocs.io/en/latest/index.html#psutil.Process.as_dict
    # Get match type and match string.
    m_str = ''
    m_type = ''
    types = [
        'name',
        'exe',
        'cmdline',
    ]
    for t in types:
        match = v_dict.get('match')
        if match:
            m_str = match[0].get(t)
            if m_str:
                m_type = t
                break

    info_list = [
        name,
        dn_max, up_max,
        dn_min, up_min,
        int(dn_pri), int(up_pri),
        dn_rate, dn_unit,
        up_rate, up_unit,
        m_type, m_str
    ]
    logging.debug(f"Process data for {name}: {info_list}")
    return info_list


def convert_config_rates_to_human(config):
    '''
    Takes single string of numbers+letters and outputs a list:
        8bit -> ['1', 'B/s']
        100kbps -> ['100', 'KB/s']
        128kbit -> ['16', 'KB/s']
        8mbit -> ['1', 'MB/s']

    Handle these pieces with the following cases:
       number:
       - [0-9]+ = quantity
       prefixes:
       - k|m|g|t = 10^[3|6|9|12]
       - ki|mi|gi|ti = 2^[10|20|30|40]
       units:
       - bit = bits per second
       - bps = bytes per second
    '''
    # re_qty = re.match('^[0-9]+', config)
    re_full = re.match('(^[0-9]+)([kmgt]?[i]?)([bipst]{3,})$', config)
    qty = float(re_full.group(1))   # 128, etc.
    pref_in = re_full.group(2)      # k or ki, etc.
    unit_in = re_full.group(3)      # bit or bps

    if unit_in == 'bit':
        # Convert bits to bytes.
        qty = qty/8

    pref_out = ''
    if len(pref_in) == 2 and pref_in[1] == 'i':
        # Take BINARY bytes * 1000/1024 to get SI bytes.
        qty = qty*1000/1024

    if pref_in == 'k' or pref_in == 'ki':
        pref_out = 'K'
    elif pref_in == 'm' or pref_in == 'mi':
        pref_out = 'M'
    elif pref_in == 'g' or pref_in == 'gi':
        pref_out = 'G'
    elif pref_in == 't' or pref_in == 'ti':
        pref_out = 'T'

    # Move "down the ladder" if less than 1.
    if qty < 1 and pref_out:
        qty = qty*1000
        if pref_out == 'K':
            pref_out = ''
        elif pref_out == 'M':
            pref_out = 'K'
        elif pref_out == 'G':
            pref_out = 'M'
        elif pref_out == 'T':
            pref_out = 'G'

    unit_out = pref_out + 'B/s'
    return ["{:.0f}".format(qty), unit_out]


def convert_config_list_units(c_list):
    # 0: scope
    # 1: max down
    # 2: max up
    # 3: min down
    # 4: min up
    c_list = c_list.copy()
    for i in range(1, 5):
        if not c_list[i]:
            continue
        h_list = convert_config_rates_to_human(c_list[i])
        c_list[i] = ' '.join(h_list)
    return c_list


def validate_yaml(yaml_file):
    """
    Determine if given file exists, has correct syntax, and has correct schema.
    """
    # Ref:
    #   https://pypi.org/project/schema/
    #   https://stackoverflow.com/questions/3262569/validating-a-yaml-document-in-python#22231372

    status = False
    file_obj = Path(yaml_file)
    myschema = schema.Schema(
        {
            schema.Optional('download'): schema.And(str),
            schema.Optional('upload'): schema.And(str),
            schema.Optional('download-minimum'): schema.And(str),
            schema.Optional('upload-minimum'): schema.And(str),
            schema.Optional('download-priority'): schema.And(int),
            schema.Optional('upload-priority'): schema.And(int),
            schema.Optional('processes'): {
                schema.Regex(r'[a-zA-z-]+'): {
                    'match': [{
                        schema.Optional('cmdline'): schema.And(str),
                        schema.Optional('exe'): schema.And(str),
                        schema.Optional('name'): schema.And(str),
                    }],
                    schema.Optional('download'): schema.And(str),
                    schema.Optional('upload'): schema.And(str),
                    schema.Optional('download-minimum'): schema.And(str),
                    schema.Optional('upload-minimum'): schema.And(str),
                    schema.Optional('download-priority'): schema.And(int),
                    schema.Optional('upload-priority'): schema.And(int),
                },
            },
        }
    )
    if file_obj.is_file():
        # Test if YAML syntax is correct.
        with open(yaml_file, 'r') as f:
            try:
                data = yaml.safe_load(f)
                # Test if YAML matches schema.
                try:
                    myschema.validate(data)
                    status = True
                except schema.SchemaError as e:
                    logging.error(e)
            except yaml.YAMLError as e:
                logging.error(e)
    else:
        logging.error(f"File does not exist: {file_obj}")

    return status


def convert_yaml_to_store(f, test=False):
    logging.info(f"Reading config from {f}")

    # Validate YAML file.
    if not validate_yaml(f):
        logging.error(f"Invalid config file: {f}")
        # Use default config file.
        logging.error("Resetting to default config.")
        if not test:
            p = subprocess.run(['pkexec', '/usr/bin/traffic-cop', '--reset'])

    # Get dict from yaml file.
    with open(f, 'r') as stream:
        try:
            content = yaml.safe_load(stream)
        except Exception as e:
            # Not likely to happen?
            logging.error(e)
            return ''

    if not content:
        # Yaml file has no viable content.
        logging.warning(f"\"{f}\" has no usable config.")
        return ''

    # Move global config keys into their own dict under a 'Global' key.
    config_dict = {}
    g_name = 'Global'
    g_config = convert_dict_to_list(g_name, content)
    config_dict[g_name] = {
        'download': g_config[1],
        'upload': g_config[2],
        'download-minimum': g_config[3],
        'upload-minimum': g_config[4],
        'download-priority': g_config[5],
        'upload-priority': g_config[6],
        'match-type': g_config[7],
        'match-str': g_config[8],
    }
    # Add entries for 'unknown TCP' and 'unknown UDP'.
    config_dict['unknown TCP'] = {
        'download': '',
        'upload': '',
        'download-minimum': '',
        'upload-minimum': '',
        'download-priority': 9,
        'upload-priority': 9,
        'match-type': '',
        'match-str': 'unknown',
    }
    config_dict['unknown UDP'] = {
        'download': '',
        'upload': '',
        'download-minimum': '',
        'upload-minimum': '',
        'download-priority': 9,
        'upload-priority': 9,
        'match-type': '',
        'match-str': 'unknown',
    }
    # Add entries for Process config keys.
    for p_name, p in content['processes'].items():
        config_dict[p_name] = p

    # Convert dict to a list store.
    store = convert_dict_to_store(config_dict)
    return store


def convert_dict_to_store(data_dict):
    store = Gtk.ListStore(
        str, str, str, str, str, int,
        int, str, str, str, str, str, str,
    )
    for k, v in data_dict.items():
        lst = convert_dict_to_list(k, v)
        logging.debug(f"New ListStore line: {lst}")
        lst = convert_config_list_units(lst)  # in-place updating of list items
        store.append(lst)
    return store
