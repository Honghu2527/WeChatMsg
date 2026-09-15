"""Launch the Windows GUI with ``python -m gui.app``."""

from __future__ import annotations

from multiprocessing import freeze_support
import tkinter as tk

from gui.main_window import MainWindow


def main() -> None:
    freeze_support()
    root = tk.Tk()
    window = MainWindow(root)
    root.protocol("WM_DELETE_WINDOW", window.close)
    root.mainloop()


if __name__ == "__main__":
    main()
