"""Progress and Live Ticker Utility Module (progress.py)."""
import sys
import time
import threading

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass


class LiveTicker:
    """Display live progress in interactive terminals or periodic newline heartbeats in background pipes."""

    def __init__(self, prefix="Processing", heartbeat_sec=15.0):
        self.prefix = prefix
        self.heartbeat_sec = heartbeat_sec
        self.stop_event = threading.Event()
        self.thread = None
        self.t0 = time.time()
        self.is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())

    def _run(self):
        if self.is_tty:
            spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            idx = 0
            while not self.stop_event.is_set():
                elapsed = time.time() - self.t0
                spin = spinner[idx % len(spinner)]
                print(f"\r  {spin} {self.prefix} [Elapsed: {elapsed:.0f}s]...", end="", flush=True)
                idx += 1
                self.stop_event.wait(0.4)
        else:
            print(f"  ► {self.prefix} ...", flush=True)
            while not self.stop_event.wait(self.heartbeat_sec):
                elapsed = time.time() - self.t0
                print(f"  ⏳ [In Progress] {self.prefix} [Elapsed: {elapsed:.0f}s]...", flush=True)

    def __enter__(self):
        self.t0 = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=0.6)
        elapsed = time.time() - self.t0
        if self.is_tty:
            if exc_type is None:
                print(f"\r  ✓ {self.prefix} - Done in {elapsed:.1f}s.                                     \n", flush=True)
            else:
                print(f"\r  ✗ {self.prefix} - Error after {elapsed:.1f}s.                                    \n", flush=True)
        else:
            if exc_type is None:
                print(f"  ✓ {self.prefix} - Done in {elapsed:.1f}s.", flush=True)
            else:
                print(f"  ✗ {self.prefix} - Error after {elapsed:.1f}s.", flush=True)
