"""CLI-mode device activation flow."""

from datetime import datetime

from src.constants.system import SystemConstants
from src.logging import get_logger
from src.ui.shared.activation import BaseActivation

logger = get_logger()


class CliActivation(BaseActivation):
    """CLI-mode device activation handler.

    Inherits from BaseActivation and only overrides the terminal output presentation methods.
    """

    def __init__(self, activation_service, init_result: dict):
        super().__init__(activation_service, init_result)

    async def run(self) -> bool:
        self._print_header()

        if not self.needs_activation():
            self._log("Device already activated; no further action needed")
            self._print_success()
            return True

        self._print_device_info()
        try:
            return await self._core_activate()
        except KeyboardInterrupt:
            self._log("\nUser interrupted the activation flow")
            return False

    # ---- BaseActivation presentation methods ----

    def _show_code(self, data: dict) -> None:
        self._print_activation_info(data)

    def _show_result(self, success: bool) -> None:
        if success:
            self._print_success()
        else:
            self._print_failure()

    def _show_error(self, msg: str) -> None:
        self._log(msg)

    # ---- Terminal presentation helpers ----

    def _print_header(self):
        print("\n" + "=" * 60)
        print(f"{SystemConstants.APP_DISPLAY_NAME} - Device activation")
        print("=" * 60)

    def _print_device_info(self):
        """Print the device information."""
        serial = self._service.get_serial_number() or "--"
        mac = self._service.get_mac_address() or "--"
        status = self._service.get_activation_status()

        print("\nDevice information:")
        print(f"  Serial number: {serial}")
        print(f"  MAC address: {mac}")

        local = status.get("local_activated", False)
        server = status.get("server_activated", False)
        consistent = status.get("status_consistent", True)

        if not consistent:
            status_text = "Re-activation required" if local and not server else "Auto-fixed"
        else:
            status_text = "Activated" if local else "Not activated"

        print(f"  Status: {status_text}")

    def _print_activation_info(self, data: dict):
        """Print the activation information."""
        code = data.get("code", "------")
        message = data.get("message", "Please visit xiaozhi.me and enter the verification code")

        print("\n" + "-" * 60)
        print("Activation information")
        print("-" * 60)
        print(f"Verification code: {' '.join(code)}")
        print(f"Notes: {message}")
        print("-" * 60)
        print("\nActivation steps:")
        print("  1. Open a browser and visit xiaozhi.me")
        print("  2. Log in to your account")
        print("  3. Choose to add a device")
        print(f"  4. Enter the verification code: {code}")
        print("  5. Confirm adding the device")

    def _print_success(self):
        print("\n" + "=" * 60)
        print("Device activated successfully!")
        print("=" * 60)
        print("Device successfully added to your account")
        print(f"Starting {SystemConstants.APP_DISPLAY_NAME}...")
        print("=" * 60 + "\n")

    def _print_failure(self):
        print("\n" + "=" * 60)
        print("Device activation failed")
        print("=" * 60)
        print("Possible reasons:")
        print("  - Unstable network connection")
        print("  - The verification code was entered incorrectly or has expired")
        print("  - The server is temporarily unavailable")
        print("\nSolutions:")
        print("  - Check the network connection")
        print("  - Run the program again to get a new verification code")
        print("  - Make sure the verification code is entered correctly")
        print("=" * 60 + "\n")

    def _log(self, message: str):
        """Print a timestamped log line."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")
        logger.info(message)
