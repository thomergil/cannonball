"""Entry point of Cannonball.app on macOS: the menu bar icon and the supervisor, in one app.

One ordinary app, opened by the user or at login, so that macOS asks for, and
applies, its Local Network permission to the camera sessions it runs.
"""

import multiprocessing


def main() -> None:
    multiprocessing.freeze_support()  # worker processes re-enter here
    from .menubar import main as menubar

    menubar()


if __name__ == "__main__":
    main()
