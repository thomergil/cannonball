"""Entry point of Cannonball.app on macOS.

The menu bar and the supervisor run in one app process, opened by the user or
at login, so macOS asks for the app's Local Network permission and applies it
to the worker processes the app starts.
"""

import multiprocessing


def main() -> None:
    multiprocessing.freeze_support()  # worker processes re-enter here
    from .menubar import main as menubar

    menubar()


if __name__ == "__main__":
    main()
