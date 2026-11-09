"""Process and file-lock primitives used by source and packaged scanner workers."""

import os
import sys
import time


def python_command(module, *args):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--signal-scanner-entry", module, *args]
    return [sys.executable, "-m", module, *args]


if os.name == "posix":
    import fcntl as locking
else:
    import msvcrt

    class locking:
        LOCK_EX = 2
        LOCK_NB = 4

        @staticmethod
        def flock(file, operation):
            fd = file if isinstance(file, int) else file.fileno()
            position = os.lseek(fd, 0, os.SEEK_CUR)
            try:
                while True:
                    os.lseek(fd, 0, os.SEEK_SET)
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        return
                    except OSError as exc:
                        if exc.errno not in (13, 11, 36):
                            raise
                        if operation & locking.LOCK_NB:
                            raise BlockingIOError() from exc
                        time.sleep(0.05)
            finally:
                os.lseek(fd, position, os.SEEK_SET)


def descriptor_path(fd):
    if sys.platform == "darwin":
        return locking.fcntl(fd, 50, b"\0" * 1024).split(b"\0", 1)[0]
    if os.name == "posix":
        return os.readlink(f"/proc/self/fd/{fd}")
    import ctypes
    from ctypes import wintypes

    get_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    get_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = get_path(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
    if not size or size >= len(buffer):
        raise OSError("Unable to resolve file descriptor")
    path = buffer.value
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path.removeprefix("\\\\?\\")


def peak_rss_bytes():
    try:
        import resource
    except ImportError:
        return None  # This optional process metric is unavailable on Windows.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
