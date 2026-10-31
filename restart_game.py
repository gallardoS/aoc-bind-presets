"""Graceful, explicitly approved restart of the existing game executable."""
import csv
import ctypes
from ctypes import wintypes as w
from pathlib import Path
import subprocess

GAME_NAMES = {'arenaofchampions.exe', 'aoc-client.exe', 'aoc_client.exe'}


class GameProcess:
    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel.OpenProcess.argtypes = [w.DWORD,w.BOOL,w.DWORD]; self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
        self.kernel.GetProcessTimes.argtypes = [w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
        self.kernel.WaitForSingleObject.argtypes = [w.HANDLE,w.DWORD]
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND,ctypes.POINTER(w.DWORD)]
        self.user.IsWindowVisible.argtypes = [w.HWND]
        self.user.PostMessageW.argtypes = [w.HWND,w.UINT,w.WPARAM,w.LPARAM]

    def identity(self, handle):
        path = ctypes.create_unicode_buffer(32768); length = w.DWORD(32768)
        if not self.kernel.QueryFullProcessImageNameW(handle,0,path,ctypes.byref(length)):
            raise ValueError('Could not locate the running game executable.')
        times = [w.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):
            raise ValueError('Could not identify the running game process.')
        created = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
        return str(Path(path.value).resolve()), created

    def running(self):
        result = subprocess.run(['tasklist','/FO','CSV','/NH'],capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
        if result.returncode: raise ValueError('Could not check whether the game is running.')
        games = []
        for row in csv.reader(result.stdout.decode('utf-8',errors='replace').splitlines()):
            if not row or row[0].lower() not in GAME_NAMES: continue
            pid = int(row[1]); handle = self.kernel.OpenProcess(0x1000,False,pid)
            if not handle: raise ValueError('Could not inspect the game. Run both apps at the same permission level.')
            try:
                path,created = self.identity(handle)
                games.append({'pid':pid,'path':path,'created':created})
            finally: self.kernel.CloseHandle(handle)
        if len(games)>1: raise ValueError('More than one game is running. Close the extra instances and try again.')
        return games[0] if games else None

    def close(self, game):
        handle = self.kernel.OpenProcess(0x101000,False,game['pid'])
        if not handle:
            # An already exited process is fine, but do not mistake access
            # denial for an exit and then apply while the game is still open.
            if self.running() is None: return
            raise ValueError('Could not close the game. Close it manually and apply again.')
        try:
            path,created = self.identity(handle)
            if path.casefold()!=game['path'].casefold() or created!=game['created']:
                raise ValueError('The game process changed. Apply again to restart the current instance.')
            windows = []
            callback_type = ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
            def visit(hwnd,param):
                pid = w.DWORD(); self.user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
                if pid.value==game['pid'] and self.user.IsWindowVisible(hwnd): windows.append(hwnd)
                return True
            callback = callback_type(visit)
            self.user.EnumWindows(callback,0)
            if not windows: raise ValueError('No game window could be closed. Close the game manually and apply again.')
            for hwnd in windows:
                if not self.user.PostMessageW(hwnd,0x10,0,0):
                    raise ValueError('Windows blocked the close request. Close the game manually and apply again.')
            if self.kernel.WaitForSingleObject(handle,15000)!=0:
                raise ValueError('The game did not close within 15 seconds. No preset was applied. Close it manually and try again.')
        finally: self.kernel.CloseHandle(handle)

    def launch(self, game):
        executable = Path(game['path'])
        if executable.name.lower() not in GAME_NAMES or not executable.is_file():
            raise ValueError('The original game executable is no longer available.')
        subprocess.Popen([str(executable)],cwd=str(executable.parent),close_fds=True)


def running_game(): return GameProcess().running()


def apply_with_restart(path,values,backup_dir,game,apply_settings,backend=None):
    backend = backend or (GameProcess() if game else None)
    if not game: return apply_settings(path,values,backup_dir),False
    backend.close(game)
    try: backup = apply_settings(path,values,backup_dir)
    except Exception as exc:
        try: backend.launch(game)
        except Exception:
            raise ValueError(f'Could not apply the preset: {exc}\nThe game also could not be reopened. Start it manually.') from None
        raise ValueError(f'Could not apply the preset: {exc}\nThe game was reopened. The preset was not fully applied.') from None
    try: backend.launch(game)
    except Exception as exc:
        raise ValueError(f'The preset was applied, but the game could not be restarted: {exc}\nStart it manually.') from None
    return backup,True
