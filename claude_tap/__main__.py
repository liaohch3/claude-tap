"""Allow running as `python -m claude_tap`."""

from claude_tap.cli import main_entry


def main() -> None:
    main_entry()


if __name__ == "__main__":
    main()
