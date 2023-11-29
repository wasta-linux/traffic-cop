# https://docs.python.org/3/distutils/setupscript.html

from setuptools import setup

setup(
    scripts=[
        'bin/tt-wrapper',
    ],
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
