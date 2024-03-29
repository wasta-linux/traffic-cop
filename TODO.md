### Planned features and outstanding issues
Please use the [Issue tracker](https://github.com/wasta-linux/traffic-cop/issues) to report any problems.

### Feature wishlist
- Work out reliable switching between VPN and default gateway interfaces/devices.
  - works well when turning off the VPN because that interface goes away
  - doesn't work when turning on the VPN because the default interface stays
- Let config table expand with window height.
- Set config in app window instead of text file.
- Consider showing more precision on higher bandwidth prefixes (e.g. M, G, T)
- Have a tray status indicator.
- Show current download/upload rates in panel.

### Other Notes
- Not building for Bionic: not compatible due to python3 version (as of 2022-03-08):
  - traffictoll/net.py; L25; value = shlex.join(value):
  - AttributeError: module shlex has no attribue "join"
  - "join" method was added to [shlex](https://docs.python.org/3/library/shlex.html#shlex.join) in python3.8, but:
    - sudo apt install python3
    - python3 --version
    - 3.6.9 (no newer version available)
  - Tried requiring python3.8 in bionic, but it still wanted to use python3 at runtime.
- Does setting a limit to 0 effectively block the process' access to the internet?
  - It doesn't appear so. Using "0bit" (0 bits per second) significantly slows
    Firefox, but it doesn't seem to completely block it.
  - Using "8bit" actually seems to come closer to being properly applied, and it seems to slow Firefox
    even more that "0bit".
