"""
Epson Thermal Printer Manager (Linux USB / ESC-POS)
Uses python-escpos over USB for Epson thermal printers.
"""

import os
import importlib
import logging
import tempfile
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()
logger = logging.getLogger('discord-printbot.printer')


def _resolve_escpos_usb_class():
    """Resolve Usb/USB class across python-escpos versions."""
    candidates = [
        ("escpos.printer", "USB"),
        ("escpos.printer", "Usb"),
        ("escpos.printer.usb", "USB"),
        ("escpos.printer.usb", "Usb"),
    ]
    for module_name, class_name in candidates:
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, class_name, None)
            if cls is not None:
                return cls
        except Exception:
            continue
    raise ImportError(
        "Could not locate USB printer class in python-escpos. "
        "Tried escpos.printer and escpos.printer.usb for Usb/USB."
    )


EscposUSB = _resolve_escpos_usb_class()

class PrinterManager:
    """Manages printing to Epson thermal printer via Linux USB ESC/POS."""
    
    def __init__(self):
        """Initialize USB settings from environment."""
        self.vendor_id = int(os.getenv("PRINTER_USB_VID", "0x04B8"), 16)
        self.product_id = int(os.getenv("PRINTER_USB_PID", "0x0202"), 16)
        self.in_ep = int(os.getenv("PRINTER_USB_IN_EP", "0x82"), 16)
        self.out_ep = int(os.getenv("PRINTER_USB_OUT_EP", "0x01"), 16)
        self.timeout = int(os.getenv("PRINTER_USB_TIMEOUT", "0"))
        self.profile = os.getenv("PRINTER_PROFILE", "default")
        self.image_max_width = max(1, int(os.getenv("PRINTER_IMAGE_MAX_WIDTH", "512")))
        # Do not probe USB here; keep startup responsive and verify explicitly later.

    def _create_printer(self):
        """Create a USB ESC/POS printer instance."""
        return EscposUSB(
            self.vendor_id,
            self.product_id,
            in_ep=self.in_ep,
            out_ep=self.out_ep,
            timeout=self.timeout,
            profile=self.profile,
        )

    def verify_printer_exists(self):
        """Verify the USB printer can be opened."""
        try:
            printer = self._create_printer()
            printer.close()
            logger.info(
                f"Using USB printer VID:PID "
                f"{self.vendor_id:04x}:{self.product_id:04x}"
            )
        except Exception as e:
            raise Exception(
                "Could not open USB printer. "
                "Check USB IDs, endpoints, and Linux permissions (udev). "
                f"Details: {e}"
            )
    
    def list_printers(self):
        """List configured USB printer target."""
        return [f"USB {self.vendor_id:04x}:{self.product_id:04x}"]
    
    def check_printer_connection(self):
        """Test printer connection"""
        try:
            printer = self._create_printer()
            printer.close()
            return True
        except Exception as e:
            raise Exception(f"Printer connection test failed: {e}")
    
    def print_message(self, message_text, cut_paper=True):
        """Print formatted message to thermal printer via USB ESC/POS.
        
        Args:
            message_text: The text to print
            cut_paper: Whether to cut the paper after printing (default True)
        """
        
        try:
            printer = self._create_printer()
            printer.text(message_text)
            if cut_paper:
                printer.text("\n\n")
                printer.cut(mode="PART")
            printer.close()
        except Exception as e:
            logger.error('Error during printing: %s', e)
            raise
    
    def print_image(self, image_path, max_width=None):
        """Print an image to thermal printer via USB ESC/POS.
        
        Args:
            image_path: Path to the image file
            max_width: Maximum width in pixels (default from PRINTER_IMAGE_MAX_WIDTH)
        """
        temp_image_path = None
        try:
            image_to_print = image_path
            effective_max_width = self.image_max_width if max_width is None else max(1, int(max_width))
            if effective_max_width > 0:
                with Image.open(image_path) as img:
                    normalized = ImageOps.exif_transpose(img)
                    if normalized.width > effective_max_width:
                        scale = effective_max_width / float(normalized.width)
                        resized_height = max(1, int(normalized.height * scale))
                        resized = normalized.resize((effective_max_width, resized_height), Image.Resampling.LANCZOS)
                        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
                            temp_image_path = tmp_file.name
                        resized.save(temp_image_path, format='PNG')
                        image_to_print = temp_image_path
                    elif normalized is not img:
                        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
                            temp_image_path = tmp_file.name
                        normalized.save(temp_image_path, format='PNG')
                        image_to_print = temp_image_path

            printer = self._create_printer()
            printer.image(image_to_print, impl="bitImageRaster")
            printer.text("\n")
            printer.close()
        except Exception as e:
            logger.error('Error printing image: %s', e)
            raise
        finally:
            if temp_image_path and os.path.exists(temp_image_path):
                try:
                    os.remove(temp_image_path)
                except OSError:
                    pass
    
    def print_qr_code(self, qr_data, scale=8, cut_paper=True):
        """Generate and print a QR code
        
        Args:
            qr_data: The data to encode (URL, text, etc.)
            scale: Size of the QR code (default 8)
            cut_paper: Whether to cut paper after printing (default True)
        """
        
        try:
            printer = self._create_printer()
            printer.set(align="center")
            printer.qr(qr_data, size=scale)
            if cut_paper:
                printer.text("\n\n")
                printer.cut(mode="PART")
            else:
                printer.text("\n")
            printer.set(align="left")
            printer.close()
            logger.info('QR code printed successfully')
        except Exception as e:
            logger.error('Error generating/printing QR code: %s', e)
            raise
    
    def print_barcode(self, barcode_data, barcode_type='CODE128', cut_paper=True):
        """Generate and print a barcode
        
        Args:
            barcode_data: The barcode data (can be any length alphanumeric)
            barcode_type: Type of barcode (default 'code128' for flexibility)
            cut_paper: Whether to cut paper after printing (default True)
        """
        
        try:
            data = barcode_data
            if not data.startswith("{"):
                data = "{B" + data

            printer = self._create_printer()
            printer.set(align="center")
            printer.barcode(
                data,
                barcode_type,
                function_type="B",
                pos="BELOW",
                height=80,
                width=2,
                align_ct=True,
                force_software=True,
            )
            if cut_paper:
                printer.text("\n\n")
                printer.cut(mode="PART")
            else:
                printer.text("\n")
            printer.set(align="left")
            printer.close()
            logger.info('Barcode printed successfully')
        except Exception as e:
            logger.error('Error generating/printing barcode: %s', e)
            raise
    
    def cut_paper(self):
        """Send cut command to printer"""
        try:
            printer = self._create_printer()
            printer.text("\n\n")
            printer.cut(mode="PART")
            printer.close()
        except Exception as e:
            logger.error('Error cutting paper: %s', e)
            raise
    
    def print_test_page(self):
        """Print a test page to verify printer is working"""
        
        test_message = """
=====================================
    DISCORD PRINTER BOT TEST PAGE
=====================================

Date: Test Print
User: System

This is a test message from the
Discord Printer Bot

The printer is working correctly!

Attachments: 0

=====================================

"""

        self.print_message(test_message)
        logger.info('Test page printed successfully')
