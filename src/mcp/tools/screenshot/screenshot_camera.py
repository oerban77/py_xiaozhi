"""
Screenshot camera implementation for capturing desktop screens.
"""

import io
import json
import sys

from src.logging import get_logger
from src.mcp.tools.camera.base_camera import BaseCamera

logger = get_logger()


class ScreenshotCamera(BaseCamera):
    """
    Desktop screenshot camera implementation.
    """

    def __init__(self):
        """
        Initialize the screenshot camera.
        """
        super().__init__()
        logger.info("Initializing ScreenshotCamera")

        # Import dependency libraries
        self._import_dependencies()

    def _import_dependencies(self):
        """
        Import the required dependency libraries.
        """
        # Detect whether PIL is available (avoids an unused-import warning)
        try:
            import importlib.util

            self._pil_available = importlib.util.find_spec("PIL.ImageGrab") is not None
            if self._pil_available:
                logger.info("PIL ImageGrab available for screenshot capture")
            else:
                logger.warning(
                    "PIL not available, will try alternative screenshot methods"
                )
        except Exception:
            self._pil_available = False
            logger.warning(
                "Failed to check PIL availability, fallback methods will be used"
            )

        # Platform-specific imports
        if sys.platform == "darwin":  # macOS
            # Use which to detect whether the system screencapture command is available
            try:
                import shutil

                self._subprocess_available = shutil.which("screencapture") is not None
                if self._subprocess_available:
                    logger.info("screencapture command available for macOS screenshot")
                else:
                    logger.warning("screencapture command not found on macOS")
            except Exception:
                self._subprocess_available = False
        elif sys.platform == "win32":  # Windows
            try:
                import ctypes

                self._win32_available = hasattr(ctypes, "windll")
                logger.info("Win32 API available for Windows screenshot")
            except ImportError:
                self._win32_available = False

    def capture(self, display_id=None) -> bool:
        """Capture the desktop screen.

        Args:
            display_id: monitor ID; None = all monitors, "main" = the main display, "secondary" = the secondary display, 1,2,3... = a specific monitor

        Returns:
            Returns True on success, False on failure
        """
        try:
            logger.info("Starting desktop screenshot capture...")

            # Try different screenshot methods
            screenshot_data = None

            # Prefer platform-specific methods (better multi-monitor support)
            if sys.platform == "darwin" and getattr(
                self, "_subprocess_available", False
            ):
                screenshot_data = self._capture_macos(display_id)
            elif sys.platform == "win32" and getattr(self, "_win32_available", False):
                screenshot_data = self._capture_windows(display_id)
            elif sys.platform.startswith("linux"):
                screenshot_data = self._capture_linux(display_id)

            # Fallback method: use PIL ImageGrab
            if not screenshot_data and self._pil_available:
                screenshot_data = self._capture_with_pil()

            if screenshot_data:
                self.set_jpeg_data(screenshot_data)
                logger.info(
                    f"Screenshot captured successfully, size: {len(screenshot_data)} bytes"
                )
                return True
            else:
                logger.error("All screenshot capture methods failed")
                return False

        except Exception as e:
            logger.error(f"Error capturing screenshot: {e}", exc_info=True)
            return False

    def _capture_with_pil(self) -> bytes:
        """Use PIL ImageGrab to capture a screenshot.

        Returns:
            Image bytes in JPEG format
        """
        try:
            import PIL.ImageGrab

            logger.debug("Capturing screenshot with PIL ImageGrab...")

            # Capture all screens (including multi-monitor setups)
            screenshot = PIL.ImageGrab.grab(all_screens=True)

            # If the image has an alpha channel (RGBA), convert it to RGB
            if screenshot.mode == "RGBA":
                # Create a white background
                from PIL import Image

                background = Image.new("RGB", screenshot.size, (255, 255, 255))
                background.paste(
                    screenshot, mask=screenshot.split()[3]
                )  # Use the alpha channel as a mask
                screenshot = background
            elif screenshot.mode not in ["RGB", "L"]:
                # Ensure the image format is compatible with JPEG
                screenshot = screenshot.convert("RGB")

            # Convert to JPEG-formatted byte data
            byte_io = io.BytesIO()
            screenshot.save(byte_io, format="JPEG", quality=85)

            return byte_io.getvalue()

        except Exception as e:
            logger.error(f"PIL screenshot capture failed: {e}", exc_info=True)
            return None

    def _capture_macos(self, display_id=None) -> bytes:
        """Capture a screenshot with the macOS system command (supports monitor selection).

        Args:
            display_id: monitor ID; None = all monitors, "main" = the main display, "secondary" = the secondary display, 1,2,3... = a specific monitor

        Returns:
            Image bytes in JPEG format
        """
        try:
            from PIL import Image

            logger.debug(
                f"Capturing screenshot with macOS screencapture command, display_id: {display_id}"
            )

            # Choose the screenshot strategy according to display_id
            if display_id is None:
                # Capture all displays and combine them
                screenshot = self._capture_all_displays_macos()
            elif display_id == "main" or display_id == 1:
                # Capture the main display
                screenshot = self._capture_single_display_macos(1)
            elif display_id == "secondary" or display_id == 2:
                # Capture the secondary display
                screenshot = self._capture_single_display_macos(2)
            elif isinstance(display_id, int) and display_id > 0:
                # Capture the specified display
                screenshot = self._capture_single_display_macos(display_id)
            else:
                logger.error(f"Invalid display_id: {display_id}")
                return None

            if not screenshot:
                logger.error("Failed to create composite screenshot")
                return None

            # Convert to JPEG
            if screenshot.mode == "RGBA":
                # Create a white background
                background = Image.new("RGB", screenshot.size, (255, 255, 255))
                background.paste(screenshot, mask=screenshot.split()[3])
                screenshot = background
            elif screenshot.mode not in ["RGB", "L"]:
                screenshot = screenshot.convert("RGB")

            # Save as JPEG byte data
            byte_io = io.BytesIO()
            screenshot.save(byte_io, format="JPEG", quality=85)

            return byte_io.getvalue()

        except Exception as e:
            logger.error(f"macOS screenshot capture failed: {e}", exc_info=True)
            return None

    def _composite_displays(self, displays):
        """Combine screenshots from multiple monitors into one image.

        Args:
            displays: list of display info

        Returns:
            The composited PIL Image object
        """
        try:
            from PIL import Image

            # Compute the composed dimensions
            # Assume monitors are arranged horizontally or vertically
            total_width = max(display["size"][0] for display in displays)
            total_height = sum(display["size"][1] for display in displays)

            # Also compute dimensions for left/right arrangements
            horizontal_width = sum(display["size"][0] for display in displays)
            horizontal_height = max(display["size"][1] for display in displays)

            # Choose the more compact arrangement
            if total_width * total_height <= horizontal_width * horizontal_height:
                # Vertical arrangement is more compact
                composite = Image.new("RGB", (total_width, total_height), (0, 0, 0))
                y_offset = 0
                for display in sorted(displays, key=lambda d: d["id"]):
                    x_offset = (total_width - display["size"][0]) // 2  # Centered
                    composite.paste(display["image"], (x_offset, y_offset))
                    y_offset += display["size"][1]
                logger.debug(f"Created vertical composite: {composite.size}")
            else:
                # Horizontal arrangement is more compact
                composite = Image.new(
                    "RGB", (horizontal_width, horizontal_height), (0, 0, 0)
                )
                x_offset = 0
                for display in sorted(displays, key=lambda d: d["id"]):
                    y_offset = (horizontal_height - display["size"][1]) // 2  # Centered
                    composite.paste(display["image"], (x_offset, y_offset))
                    x_offset += display["size"][0]
                logger.debug(f"Created horizontal composite: {composite.size}")

            return composite

        except Exception as e:
            logger.error(f"Failed to composite displays: {e}", exc_info=True)
            return None

    def _capture_windows(self, display_id=None) -> bytes:
        """Capture a screenshot with the Windows API.

        Args:
            display_id: monitor ID (not implemented yet; uses the virtual screen)

        Returns:
            Image bytes in JPEG format
        """
        try:
            import ctypes
            import ctypes.wintypes

            from PIL import Image

            logger.debug(
                f"Capturing screenshot with Windows API, display_id: {display_id}"
            )

            # Get the virtual screen size (including all displays)
            user32 = ctypes.windll.user32
            # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
            virtual_left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            virtual_top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
            virtual_width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            virtual_height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN

            screensize = (virtual_width, virtual_height)
            screen_offset = (virtual_left, virtual_top)

            # Create device context
            hdc = user32.GetDC(None)
            hcdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
            hbmp = ctypes.windll.gdi32.CreateCompatibleBitmap(
                hdc, screensize[0], screensize[1]
            )
            ctypes.windll.gdi32.SelectObject(hcdc, hbmp)

            # Copy the virtual screen to a bitmap (including all displays)
            ctypes.windll.gdi32.BitBlt(
                hcdc,
                0,
                0,
                screensize[0],
                screensize[1],
                hdc,
                screen_offset[0],
                screen_offset[1],
                0x00CC0020,
            )

            # Get bitmap data
            # ctypes.wintypes does not define BITMAPINFO, so build the structure
            # explicitly (BITMAPINFOHEADER + 3 DWORDS for the color table).
            class _BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", ctypes.wintypes.DWORD),
                    ("biWidth", ctypes.wintypes.LONG),
                    ("biHeight", ctypes.wintypes.LONG),
                    ("biPlanes", ctypes.wintypes.WORD),
                    ("biBitCount", ctypes.wintypes.WORD),
                    ("biCompression", ctypes.wintypes.DWORD),
                    ("biSizeImage", ctypes.wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.wintypes.LONG),
                    ("biYPelsPerMeter", ctypes.wintypes.LONG),
                    ("biClrUsed", ctypes.wintypes.DWORD),
                    ("biClrImportant", ctypes.wintypes.DWORD),
                ]

            class _BITMAPINFO(ctypes.Structure):
                _fields_ = [
                    ("bmiHeader", _BITMAPINFOHEADER),
                    ("bmiColors", ctypes.wintypes.DWORD * 3),
                ]

            bmpinfo = _BITMAPINFO()
            bmpinfo.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmpinfo.bmiHeader.biWidth = screensize[0]
            bmpinfo.bmiHeader.biHeight = -screensize[1]  # Negative values mean top-to-bottom
            bmpinfo.bmiHeader.biPlanes = 1
            bmpinfo.bmiHeader.biBitCount = 32
            bmpinfo.bmiHeader.biCompression = 0

            # Allocate buffer
            buffer_size = screensize[0] * screensize[1] * 4
            buffer = ctypes.create_string_buffer(buffer_size)

            # Get pixel data
            ctypes.windll.gdi32.GetDIBits(
                hcdc, hbmp, 0, screensize[1], buffer, ctypes.byref(bmpinfo), 0
            )

            # Clean up resources
            ctypes.windll.gdi32.DeleteObject(hbmp)
            ctypes.windll.gdi32.DeleteDC(hcdc)
            user32.ReleaseDC(None, hdc)

            # Convert to a PIL Image
            image = Image.frombuffer("RGBA", screensize, buffer, "raw", "BGRA", 0, 1)
            image = image.convert("RGB")

            # Convert to JPEG byte data
            byte_io = io.BytesIO()
            image.save(byte_io, format="JPEG", quality=85)

            return byte_io.getvalue()

        except Exception as e:
            logger.error(f"Windows screenshot capture failed: {e}", exc_info=True)
            return None

    def _capture_linux(self, display_id=None) -> bytes:
        """Capture a screenshot via Linux system commands.

        Args:
            display_id: monitor ID (not implemented yet; uses the default monitor)

        Returns:
            Image bytes in JPEG format
        """
        try:
            import os
            import subprocess
            import tempfile

            logger.debug(
                f"Capturing screenshot with Linux screenshot commands, display_id: {display_id}"
            )

            # Try different Linux screenshot tools
            screenshot_commands = [
                ["gnome-screenshot", "-f"],  # GNOME
                ["scrot"],  # scrot
                ["import", "-window", "root"],  # ImageMagick
            ]

            for cmd_base in screenshot_commands:
                try:
                    # Create temporary file
                    with tempfile.NamedTemporaryFile(
                        suffix=".jpg", delete=False
                    ) as temp_file:
                        temp_path = temp_file.name

                    # Build the full command
                    cmd = cmd_base + [temp_path]

                    # Execute command
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=10
                    )

                    if result.returncode == 0 and os.path.exists(temp_path):
                        # Read screenshot data
                        with open(temp_path, "rb") as f:
                            screenshot_data = f.read()

                        # Clean up the temporary file
                        os.unlink(temp_path)

                        logger.debug(
                            f"Successfully captured screenshot with: {' '.join(cmd_base)}"
                        )
                        return screenshot_data
                    else:
                        # Clean up the temporary file
                        if os.path.exists(temp_path):
                            os.unlink(temp_path)

                except subprocess.TimeoutExpired:
                    logger.warning(
                        f"Screenshot command timed out: {' '.join(cmd_base)}"
                    )
                except FileNotFoundError:
                    logger.debug(f"Screenshot tool not found: {' '.join(cmd_base)}")
                except Exception as e:
                    logger.debug(f"Screenshot command failed {' '.join(cmd_base)}: {e}")

            return None

        except Exception as e:
            logger.error(f"Linux screenshot capture failed: {e}", exc_info=True)
            return None

    def analyze(self, question: str, image_data: bytes | None = None) -> str:
        try:
            logger.info(f"Analyzing screenshot with question: {question}")

            # Prefer the photo camera injected at registration time (sharing the vision URL/token)
            camera_instance = getattr(self, "_photo_camera_ref", None)
            buf = image_data if image_data is not None else self.jpeg_data["buf"]
            if camera_instance is not None:
                return camera_instance.analyze(question, image_data=buf)

            from src.mcp.tools.camera import create_camera

            return create_camera().analyze(question, image_data=buf)

        except Exception as e:
            logger.error(f"Error analyzing screenshot: {e}", exc_info=True)
            return json.dumps(
                {"success": False, "message": f"Failed to analyze screenshot: {e}"}
            )

    def _capture_single_display_macos(self, display_num):
        """Capture a single macOS display.

        Args:
            display_num: monitor number (1, 2, 3, ...)

        Returns:
            PIL Image object
        """
        try:
            import os
            import subprocess
            import tempfile

            from PIL import Image

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_path = temp_file.name

            cmd = [
                "screencapture",
                "-D",
                str(display_num),
                "-x",
                "-t",
                "png",
                temp_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0 and os.path.exists(temp_path):
                try:
                    img = Image.open(temp_path)
                    screenshot = img.copy()
                    os.unlink(temp_path)
                    logger.debug(f"Captured display {display_num}: {screenshot.size}")
                    return screenshot
                except Exception as e:
                    logger.error(f"Failed to read display {display_num}: {e}", exc_info=True)
                    os.unlink(temp_path)
                    return None
            else:
                logger.error(
                    f"screencapture failed for display {display_num}: {result.stderr}"
                )
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                return None

        except Exception as e:
            logger.error(f"Single display capture failed: {e}", exc_info=True)
            return None

    def _capture_all_displays_macos(self):
        """Capture all macOS monitors and composite them.

        Returns:
            The composited PIL Image object
        """
        try:
            import os
            import subprocess
            import tempfile

            from PIL import Image

            # Detect all available displays
            displays = []
            for display_id in range(1, 5):  # Detect up to 4 displays
                with tempfile.NamedTemporaryFile(
                    suffix=".png", delete=False
                ) as temp_file:
                    temp_path = temp_file.name

                cmd = [
                    "screencapture",
                    "-D",
                    str(display_id),
                    "-x",
                    "-t",
                    "png",
                    temp_path,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode == 0 and os.path.exists(temp_path):
                    try:
                        img = Image.open(temp_path)
                        displays.append(
                            {
                                "id": display_id,
                                "size": img.size,
                                "image": img.copy(),
                                "path": temp_path,
                            }
                        )
                        logger.debug(f"Found display {display_id}: {img.size}")
                    except Exception as e:
                        logger.debug(f"Failed to read display {display_id}: {e}")
                        os.unlink(temp_path)
                else:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)

            if not displays:
                logger.error("No displays found")
                return None

            # Clean up the temporary files
            for display in displays:
                try:
                    os.unlink(display["path"])
                except Exception as e:
                    logger.debug(f"Failed to clean screenshot temp files: {e}")

            if len(displays) == 1:
                # Single display: return directly
                return displays[0]["image"]
            else:
                # Multiple displays: need to compose
                logger.debug(f"Compositing {len(displays)} displays")
                return self._composite_displays(displays)

        except Exception as e:
            logger.error(f"All displays capture failed: {e}", exc_info=True)
            return None
