# tt-bandwidth-manager-gui
This package installs **Traffic Cop** to help you manage bandwidth usage by app or process.

![Traffic Cop](data/traffic-cop.png)

### Features
- Shows config (displayed in "B/s" regardless of unit used in the config file).
- Shows live upload and download rates globally and for each managed process.

### Limitations
Traffic Cop relies on **[nethogs](https://github.com/raboof/nethogs)** to track network usage. As of version 0.8.5-2, **nethogs** does not track udp packets, which are used by many VoIP apps, including **Zoom** and **Skype**, and thus **Traffic Cop** also doesn't show reliable upload and download rates for udp traffic. However, the backend app, **tt**, *does* properly manage this udp traffic. You can confirm it for yourself if you have a pay-per-MB internet plan and check the counter provided by your ISP periodically during a call to verify that the rate you've set is being properly applied.

### More information
See the README for the **tt-bandwidth-manager** package found at https://github.com/wasta-linux/tt-bandwidth-manager for configuration explanation and other information.

*This app uses an icon based on the "traffic-police" icon created by [Freepik](https://www.flaticon.com/authors/freepik) and found at https://www.flaticon.com/.*
