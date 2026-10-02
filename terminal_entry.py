"""Entry point for terminal packages, which default to the full-screen TUI."""

from main import main


if __name__ == "__main__":
    raise SystemExit(main(default_mode="tui"))
