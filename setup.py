# https://docs.python.org/3/distutils/setupscript.html

import glob
from distutils.core import setup
from pathlib import Path

# Get version number from debian/changelog.
changelog = Path(__file__).parents[0] / 'debian' / 'changelog'
with open(changelog) as f:
    first_line = f.readline()
# 2nd term in 1st line; need to remove parentheses.
version = first_line.split()[1][1:-1]

setup(
    name='Traffic Cop',
    version=version,
    description="Manage bandwidth usage by app or process.",
    author="Nate Marti",
    author_email="nate_marti@sil.org",
    url="https://github.com/wasta-linux/tt-bandwidth-manager-gui",
    packages=['trafficcop'],
    package_data={'trafficcop': ['README.md']},
    scripts=[
        'bin/traffic-cop',
        'bin/tt-wrapper',
    ],
    data_files=[
        ('share/polkit-1/actions', glob.glob('data/actions/*')),
        ('share/icons/hicolor/scalable/apps', glob.glob('data/icons/*.svg')),
        ('share/traffic-cop/ui', glob.glob('data/ui/*.glade')),
        ('share/applications', glob.glob('data/*.desktop')),
    ]
)
