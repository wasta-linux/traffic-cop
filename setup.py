# https://docs.python.org/3/distutils/setupscript.html

import glob
from setuptools import setup
from pathlib import Path

deb_pkg = 'traffic-cop'
py3_pkg = 'trafficcop'

# # Get version number from debian/changelog.
# changelog = Path(__file__).parents[0] / 'debian' / 'changelog'
# with open(changelog) as f:
#     first_line = f.readline()
# # 2nd term in 1st line; need to remove parentheses.
# version = first_line.split()[1][1:-1]
# Get version from trafficcop.config.VERSION.
with open('trafficcop/config.py') as f:
    for line in f:
        if line.startswith('VERSION'):
            version = line.split('=')[1].replace("'", "").strip()
            break

setup(
    name='Traffic Cop',
    version=version,
    description="Manage bandwidth usage by app or process.",
    author="Nate Marti",
    author_email="nate_marti@sil.org",
    url=f"https://github.com/wasta-linux/{deb_pkg}",
    packages=[py3_pkg],
    package_data={py3_pkg: ['README.md']},
    scripts=[
        'bin/traffic-cop',
        'bin/tt-wrapper',
    ],
    data_files=[
        ('share/polkit-1/actions', glob.glob('data/actions/*.policy')),
        ('share/icons/hicolor/scalable/apps', glob.glob('data/icons/*.svg')),
        (f"share/{deb_pkg}/ui", glob.glob('data/ui/*.glade')),
        (f"share/{deb_pkg}", ['data/traffic-cop.yaml.default']),
        ('share/applications', glob.glob('data/applications/*.desktop')),
        ('lib/systemd/system-preset', ['data/traffic-cop.preset']),
    ]
)
