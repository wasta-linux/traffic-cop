# https://docs.python.org/3/distutils/setupscript.html

from setuptools import setup

# dh-python in focal can't use pyproject.
with open('trafficcop/config.py') as f:
    for line in f:
        if line.startswith('VERSION'):
            version = line.split('=')[1].replace("'", "").strip()
            break

setup(
    name='traffic-cop',
    version=version,
    # description="Manage bandwidth usage by app or process.",
    author="Nate Marti",
    author_email="nate_marti@sil.org",
    # url=f"https://github.com/wasta-linux/{deb_pkg}",
    packages=['trafficcop'],
    scripts=[
        'bin/tt-wrapper',
    ],
    entry_points={
        'gui_scripts': [
            'traffic-cop = trafficcop.app:main',
        ],
    },
    # Handling direct file installation here rather than with debian/install.
    data_files=[
        ('share/polkit-1/actions', ['data/actions/org.wasta.apps.traffic-cop.policy']),
        ('share/icons/hicolor/scalable/apps', ['data/icons/traffic-cop.svg']),
        ('share/traffic-cop/ui', ['data/ui/mainwindow.glade']),
        ('share/traffic-cop', ['data/traffic-cop.yaml.default']),
        ('share/applications', ['data/applications/traffic-cop.desktop']),
        ('lib/systemd/system-preset', ['data/traffic-cop.preset']),
    ]
)
