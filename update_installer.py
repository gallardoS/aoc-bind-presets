"""Keep an independent update window visible across the manager restart."""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from app_updates import OWNER, install_update, read_install_manifest, wait_for_exit
from wc3_theme import install as install_theme


def manager_visible(target):
    """Compatibility with older releases that do not send a readiness marker."""
    user=ctypes.WinDLL('user32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    user.IsWindowVisible.argtypes=[w.HWND]
    user.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    visible=False
    callback_type=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    def visit(hwnd,param):
        nonlocal visible
        if not user.IsWindowVisible(hwnd):return True
        title=ctypes.create_unicode_buffer(256);user.GetWindowTextW(hwnd,title,len(title))
        if title.value!='AoC-bind-presets':return True
        pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        handle=kernel.OpenProcess(0x1000,False,pid.value)
        if not handle:return True
        try:
            path=ctypes.create_unicode_buffer(32768);length=w.DWORD(len(path))
            if kernel.QueryFullProcessImageNameW(handle,0,path,ctypes.byref(length)):
                visible=Path(path.value).resolve()==target.resolve()
        finally:kernel.CloseHandle(handle)
        return not visible
    callback=callback_type(visit)
    user.EnumWindows.argtypes=[callback_type,w.LPARAM]
    user.EnumWindows(callback,0)
    return visible


class InstallerWindow:
    def __init__(self,root,manifest_path,installer=install_update,visible=manager_visible):
        self.root=root;self.path=Path(manifest_path)
        self.manifest,self.stage,self.target=read_install_manifest(self.path)
        self.installer=installer;self.visible=visible
        self.results=queue.SimpleQueue();self.installed=False;self.deadline=None;self.failed=False
        root.withdraw();root.title('Updating AoC-bind-presets');root.resizable(False,False)
        resources=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
        install_theme(root,resources);root.iconbitmap(default=str(resources/'icons'/'app.ico'))
        root.protocol('WM_DELETE_WINDOW',lambda:None)
        panel=ttk.Frame(root,padding=24);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Updating to v'+self.manifest['version']+'…',style='Heading.TLabel').pack(anchor='w',pady=(0,14))
        ttk.Style(root).configure('Update.Horizontal.TProgressbar',background='#bfa140',troughcolor='#211c13',bordercolor='#8e7135',lightcolor='#e8ca65',darkcolor='#6e541b',thickness=18)
        self.bar=ttk.Progressbar(panel,length=380,maximum=100,value=100,style='Update.Horizontal.TProgressbar');self.bar.pack(fill='x')
        self.detail=ttk.Label(panel,text='Restarting the app…',wraplength=380);self.detail.pack(anchor='w',pady=(10,0))
        self.buttons=ttk.Frame(panel);self.buttons.pack(fill='x')
        root.update_idletasks()
        position=self.manifest.get('position')
        if not isinstance(position,list) or len(position)!=2 or not all(isinstance(n,int) for n in position):
            position=[(root.winfo_screenwidth()-root.winfo_reqwidth())//2,(root.winfo_screenheight()-root.winfo_reqheight())//2]
        x,y=position;root.geometry(f'+{max(0,x)}+{max(0,y)}');root.deiconify()
        if os.name=='nt':
            user=ctypes.WinDLL('user32')
            user.GetAncestor.argtypes=[w.HWND,w.UINT];user.GetAncestor.restype=w.HWND
            user.SetWindowPos.argtypes=[w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT]
            user.SetWindowPos(user.GetAncestor(root.winfo_id(),2),None,x,y,0,0,0x15)

    def marker(self,name):
        path=self.stage/name;temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'owner':OWNER}),encoding='utf-8');os.replace(temporary,path)

    def start(self):
        # Announce readiness only after the independent window is mapped.
        if not self.root.winfo_viewable():self.root.after(50,self.start);return
        self.root.update_idletasks()
        self.marker('helper-ready.json')
        threading.Thread(target=self.worker,daemon=True).start()
        self.root.after(50,self.poll)

    def worker(self):
        try:
            deadline=time.monotonic()+60
            while True:
                if (self.stage/'cancel-update.json').exists():
                    self.results.put(('cancelled',None));return
                try:wait_for_exit(self.manifest['pids'],timeout=.25);break
                except ValueError:
                    if time.monotonic()>deadline:raise
            if (self.stage/'cancel-update.json').exists():
                self.results.put(('cancelled',None));return
            self.results.put(('opening',None))
            # The old manager has exited; installation and launch stay off the UI thread.
            self.installer(self.path,wait=lambda pids:None)
            self.results.put(('installed',None))
        except Exception:
            self.results.put(('error','The app could not be updated. Please open the manager again.'))

    def poll(self):
        try:
            while True:
                kind,value=self.results.get_nowait()
                if kind=='cancelled':self.finish();return
                if kind=='error':self.show_error(value);return
                if kind=='opening':self.detail.configure(text='Installing the update and opening the manager…')
                if kind=='installed':
                    self.installed=True;self.deadline=time.monotonic()+90
                    self.detail.configure(text='Opening the manager…')
        except queue.Empty:pass
        if self.installed:
            ready=self.stage/'manager-ready.json'
            try:confirmed=ready.exists() and json.loads(ready.read_text(encoding='utf-8')).get('owner')==OWNER
            except (OSError,ValueError):confirmed=False
            if confirmed or self.visible(self.target):self.finish();return
            if time.monotonic()>self.deadline:
                self.show_error('The manager has not opened. Please start it manually.');return
        self.root.after(50,self.poll)

    def finish(self):
        self.marker('helper-done.json');self.root.destroy()

    def show_error(self,message):
        self.failed=True;self.detail.configure(text=message)
        self.root.protocol('WM_DELETE_WINDOW',self.finish)
        ttk.Button(self.buttons,text='Close',command=self.finish).pack(side='right',pady=(16,0))


def run_installer(manifest_path):
    read_install_manifest(manifest_path)
    if os.name=='nt':
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:pass
    root=tk.Tk();window=InstallerWindow(root,manifest_path)
    root.after(0,window.start);root.mainloop()
    return not window.failed
