"""Entry point of the standalone TrackSound app (built with PyInstaller).

Double-clicking the app runs this: it starts the local server and opens the browser.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()          # worker processes re-enter here in a frozen app
    from tracksound.cli import main

    main(sys.argv[1:])
