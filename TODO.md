### Planned features and outstanding issues
- Fallback to previous config if error in updated config file.
- Solve supposed python dependency issue on "aiocontextvars" in Ubuntu bionic 18.04

### Feature wishlist
- Let config table expand with window height.
- Set config in app window instead of text file.
- Consider showing more precision on higher bandwidth prefixes (e.g. M, G, T)
- Have a tray status indicator.
- Show current download/upload rates in panel.
- Use "x-terminal-emulator" instead of explicitly calling "gnome-terminal".
- Work out reliable switching between VPN and default gateway interfaces/devices.
  - works well when turning off the VPN because that interface goes away
  - doesn't work when turning on the VPN because the default interface stays

### Other Notes
- Does setting a limit to 0 effectively block the process' access to the internet?
  - It doesn't appear so. Using "0bit" (0 bits per second) significantly slows
    Firefox, but it doesn't seem to completely block it. Using "8bit" actually
    seems to come closer to being properly applied, and it seems to slow Firefox
    even more that "0bit".
