# CoGM Logger
A tool for Black Desert Online to log combat messages, with direct upload to [CoGM](https://cogm.app) war analytics.

Made from [Ikusa Logger](https://github.com/sch-28/ikusa_logger), by [sch-28](https://github.com/sch-28) (ORACLE#7672). The capture engine, config calibration, and log format are his work. This fork adds the CoGM upload integration and CoGM branding.

https://user-images.githubusercontent.com/42447473/184521641-e66a6bc4-191f-4c60-ae56-5172b052ec09.mp4

Visualize your captured logs with sch-28's [ikusa website](https://github.com/sch-28/ikusa), or upload them straight to your guild's [CoGM](https://cogm.app) event log from the app.

## Prerequisites

### Windows
- [Npcap - 1.7.8](https://npcap.com/dist/)
- [Node.js - 16+](https://nodejs.org/en/download/)
- [Python - 3+](https://www.python.org/downloads/)
  - In the installer, make sure to check "Add Python to environment variables"

### Linux
```
nodejs libcap python3 patchelf
```

## Installation
1. Clone the repository
2. Make sure you have the prerequisites installed
3. Run the build script for your platform:
   - Windows: `build.bat`
   - Linux: `build.sh`

## Usage
1. Start the logger:
   - Windows: `cogm-logger-win_x64.exe` located in `/dist/cogm-logger/`
   - Linux: `start.sh`
2. Click on the `Record` button
3. After you are done recording, make sure to order the names of the players in the correct order!
The order should be: `Family-Name-1 kills/died to Family-Name-2 from Enemy-Guild`
4. Download the logs by clicking `Save` or upload the logs directly to the website by clicking `Upload`

If you noticed that you have chosen the wrong name order, you can open the `.log` file again with the logger and adjust the names.

https://github.com/sch-28/ikusa_logger/assets/42447473/ebcd67f0-c43a-4d12-b38d-79a7542e92ed

## Protocol research: full payload capture
The normal logger keeps only a 300-byte window around the combat-log
identifier and pulls out four fields. To study the rest of the protocol
(gear, class, damage, position, objectives), the capture engine has a full
mode that records the entire TCP payload of every packet from BDO's servers,
losslessly. Run the bundled logger exe directly:

```
logger.exe -F -o captures/war.log
```

This writes two files alongside each other:
- `war.pcap` — full packets, open in Wireshark or read with scapy
- `war.jsonl` — one line per packet: `{time, src, dst, sport, dport, seq, len, hex}`,
  for grepping and diffing payloads to find field offsets

It captures the same unencrypted server traffic the combat logger already
reads, just in full. It does not touch live combat logging.

## Startup Issue
If you are unable to start the regular logger, try starting it with the `--mode=browser` argument.

## Need help?
For CoGM upload questions, join the [CoGM support server](https://discord.gg/rC4JEjEgnh).
For the original logger, sch-28 is on Discord: sch.28
