"""Allow running as `python -m claude_tap`."""

import asyncio
import sys

from claude_tap import async_main, parse_args


def main():
    argv = sys.argv[1:]
    if "--" in argv:
        # Explicit separator: everything after "--" goes to claude
        idx = argv.index("--")
        our_args = argv[:idx]
        claude_args = argv[idx + 1 :]
        args = parse_args(our_args)
        args.claude_args = claude_args
    else:
        # No separator: parse_args handles splitting via parse_known_args
        args = parse_args(argv)
        # claude_args already set by parse_args
    try:
        code = asyncio.run(async_main(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
