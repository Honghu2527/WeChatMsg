"""Tk-safe background task runner."""

from __future__ import annotations

import queue
from concurrent.futures import ThreadPoolExecutor


class BackgroundTasks:
    def __init__(self, root):
        self.root = root
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wechat-gui")
        self.events = queue.Queue()
        self.root.after(100, self._drain)

    def submit(self, function, on_success, on_error):
        future = self.executor.submit(function)

        def finished(done):
            try:
                self.events.put((on_success, done.result()))
            except Exception as exc:
                self.events.put((on_error, exc))

        future.add_done_callback(finished)

    def post(self, callback, *args):
        self.events.put((callback, args, True))

    def _drain(self):
        try:
            while True:
                event = self.events.get_nowait()
                if len(event) == 3:
                    callback, args, _ = event
                    callback(*args)
                else:
                    callback, value = event
                    callback(value)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
