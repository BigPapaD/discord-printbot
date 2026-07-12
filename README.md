# discord-printbot

A Discord bot that prints Discord messages, images, QR codes, and barcodes to an Epson thermal printer over USB ESC/POS.

This repository is configured for Epson TM-T88VI on Linux with USB ID `04b8:0202`.

## Features

- Prints messages when they contain `#print` or `#print-last N` (use `-nc` to skip cutting)
- Supports `#qr`, `#barcode`, `#func`, and `#cut` commands
- Prints attachments and embedded images
- Uses USB ESC/POS commands directly (no Windows print spooler)

## Requirements

- Linux machine with USB access to printer
- Python 3.11+
- Epson TM-T88VI (or compatible ESC/POS USB printer)
- `libusb` installed on host OS

Install libusb packages:

```bash
sudo apt update
sudo apt install -y libusb-1.0-0 libusb-1.0-0-dev
```

## Setup

1. Create virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Copy environment file:

```bash
cp .env.example .env
```

3. Edit `.env`:

```env
DISCORD_TOKEN=your_bot_token_here
PRINTER_USB_VID=0x04B8
PRINTER_USB_PID=0x0202
PRINTER_USB_IN_EP=0x82
PRINTER_USB_OUT_EP=0x01
PRINTER_USB_TIMEOUT=0
PRINTER_PROFILE=default
PRINTER_IMAGE_MAX_WIDTH=512
MAX_DOWNLOAD_BYTES=26214400
PRINT_COOLDOWN_SECONDS=3
PRINT_ALLOWED_ROLE_IDS=
```

`PRINT_ALLOWED_ROLE_IDS` can be left empty to allow all users, or set to a comma-separated list of Discord role IDs to restrict print commands.
`PRINTER_IMAGE_MAX_WIDTH` controls image downscaling for your paper width. Lower it if image edges are still cut off.
`MAX_DOWNLOAD_BYTES` limits attachment/embed downloads to avoid memory or disk spikes.

4. Optional: list detected USB devices:

```bash
python list_printers.py
```

5. Run the bot:

```bash
python bot.py
```

## Linux USB Permissions (Important)

If printer access fails with permission errors, create udev rules:

```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="04b8", ATTR{idProduct}=="0202", MODE="0666", GROUP="lp"' | sudo tee /etc/udev/rules.d/99-epson-tm.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug/replug the printer (or reboot).

## Run On Boot (systemd)

This repo includes a service template: `discord-printbot.service`.

1. Edit placeholders in the service file:

- Replace `YOUR_LINUX_USER`
- Confirm `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` paths

2. Install and enable service:

```bash
sudo cp discord-printbot.service /etc/systemd/system/discord-printbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now discord-printbot.service
```

3. Check service state:

```bash
sudo systemctl status discord-printbot.service
journalctl -u discord-printbot.service -f
```

4. Common service commands:

```bash
sudo systemctl restart discord-printbot.service
sudo systemctl stop discord-printbot.service
sudo systemctl start discord-printbot.service
sudo systemctl disable discord-printbot.service
```

If the service starts but cannot print, verify the bot user is in group `lp` and your udev rule is active.

## Usage

1. Reply to a message with `#print` to print the original message.
2. Send `#print` in a message to print that message directly.
3. Send `#print -nc` to print without cutting paper.
4. Send `#print-last 5` to print last five messages.
5. Send `#qr https://example.com` to print a QR code.
6. Send `#barcode 123456789` to print a Code128 barcode.
7. Send `#func x^{2}=49(1-y^{2})` to plot and print an equation.
8. Optional for `#func`: `--x=min:max --y=min:max --res=900` (example: `#func y=sin(x) --x=-12:12 --y=-2:2 --res=1200`).
9. Send `#cut` to manually cut paper.
10. Send `#help` to list all bot commands.

## Troubleshooting

- `DISCORD_TOKEN not found`: verify `.env` exists and token is set.
- USB open/claim errors: confirm udev rule and reconnect printer.
- Wrong endpoint errors: verify `PRINTER_USB_IN_EP`/`PRINTER_USB_OUT_EP` in `.env`.
- Printer not found: confirm VID/PID with `python list_printers.py`.
- Print command denied: check `PRINT_ALLOWED_ROLE_IDS` and user roles.
- Print command cooldown: tune `PRINT_COOLDOWN_SECONDS` in `.env`.

If bot startup appears to exit with no output, run these checks:

```bash
python -u bot.py
python -c "import bot; print(bot.__file__)"
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('TOKEN_SET=', bool((os.getenv('DISCORD_TOKEN') or '').strip()))"
```

Expected behavior is visible startup lines including "Starting Discord Printer Bot..." and "Attempting Discord login...".

If you still get no output, run the bundled diagnostic script:

```bash
python doctor.py
```

Then paste the full output. It reports Python path, bot path, token loading status, and USB printer check results.

## Notes

- This version no longer depends on Windows print APIs.
- Text, image, QR, barcode, and cut all use ESC/POS USB commands.
