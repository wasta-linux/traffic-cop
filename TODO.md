### Planned features and outstanding issues
- Fix under-reported of live usage for each application (missing UDP traffic?)
- Solve supposed python dependency issue on "aiocontextvars" in Ubuntu bionic 18.04

### Feature wishlist
- Develop a decent integration test suite.
- Let config table expand with window height.
- Set config in app window instead of text file.
- Consider showing more precision on higher bandwidth prefixes (e.g. M, G, T)
- Have a tray status indicator.
- Show current download/upload rates in panel.
- Use "x-terminal-emulator" instead of explicitly calling "gnome-terminal".

### Other
- does setting a limit to 0 effectively block the process' access to the internet?
- consider using python for tt-wrapper instead of bash
- work out reliable switching between VPN and default gateway interfaces/devices.
  - works well when turning off the VPN because that interface goes away
  - doesn't work when turning on the VPN because the default interface stays
- improve bandwidth accounting (nethogs on 20.04 ignores UDP traffic)
  - Take advantage of UDP tracking in nethogs 0.8.6-1 in jammy.
    https://github.com/raboof/nethogs/releases/tag/v0.8.6
  - Maybe find helpful details from tc command; e.g.
    ```bash
    tc -s class show dev ifb0
    ```
