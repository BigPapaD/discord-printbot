"""List USB thermal printers on Linux/macOS using pyusb."""

import usb.core


def list_printers():
    """List connected USB devices and highlight Epson printers."""
    print("\n" + "=" * 60)
    print("Available USB Printers (pyusb)")
    print("=" * 60 + "\n")

    devices = list(usb.core.find(find_all=True))
    if not devices:
        print("No USB devices found.")
        return

    epson_devices = []
    for device in devices:
        vid = f"{device.idVendor:04x}"
        pid = f"{device.idProduct:04x}"
        line = f"VID:PID {vid}:{pid}"
        if device.idVendor == 0x04B8:
            epson_devices.append((vid, pid))
            line += "  <-- Epson"
        print(line)

    print("\n" + "=" * 60)
    if epson_devices:
        print("Suggested .env settings for Epson device(s):")
        for vid, pid in epson_devices:
            print(f"PRINTER_USB_VID=0x{vid.upper()}")
            print(f"PRINTER_USB_PID=0x{pid.upper()}")
            print("-")
    else:
        print("No Epson USB device (VID 04b8) detected right now.")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    try:
        list_printers()
    except Exception as e:
        print(f"Error listing printers: {e}")
        print("\nMake sure pyusb is installed:")
        print("  pip install pyusb")
