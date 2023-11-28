# https://docs.python.org/3/distutils/setupscript.html

import glob
from setuptools import setup

deb_pkg = 'traffic-cop'

setup(
    scripts=[
        'bin/tt-wrapper',
    ],
    # Handling direct file installation here rather than with debian/install.
    data_files=[
        ('share/icons/hicolor/scalable/apps', glob.glob('data/icons/*.svg')),
        (f"share/{deb_pkg}/ui", glob.glob('data/ui/*.glade')),
        (f"share/{deb_pkg}", ['data/traffic-cop.yaml.default']),
        ('share/applications', glob.glob('data/applications/*.desktop')),
        ('lib/systemd/system-preset', ['data/traffic-cop.preset']),
    ]
)
