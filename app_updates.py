"""Verified GitHub releases and a separate Windows replacement helper."""
from dataclasses import dataclass
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import ttk

REPOSITORY='gallardoS/aoc-bind-presets'
LATEST_URL=f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
ASSET_NAME='AoC-bind-presets.exe'
OWNER='AoC-bind-presets updater'
MAX_DOWNLOAD=256*1024*1024


def version_tuple(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(value).strip())
    if not match:raise ValueError('Unrecognized release version.')
    return tuple(int(part) for part in match.groups())


def current_version():
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    value=(root/'VERSION').read_text(encoding='utf-8-sig').strip()
    version_tuple(value)
    return value


class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parsed=urllib.parse.urlsplit(newurl)
        if parsed.scheme!='https' or not (parsed.hostname in ('github.com','api.github.com') or (parsed.hostname or '').endswith('.githubusercontent.com')):
            raise ValueError('Unexpected update download location.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def open_url(url):
    request=urllib.request.Request(url,headers={'User-Agent':'AoC-bind-presets updater','Accept':'application/vnd.github+json' if url==LATEST_URL else 'application/octet-stream'})
    return urllib.request.build_opener(HTTPSRedirects()).open(request,timeout=20)


def latest_release():
    with open_url(LATEST_URL) as response:
        raw=response.read(2_000_001)
    if len(raw)>2_000_000:raise ValueError('Unexpected release response.')
    return json.loads(raw)


def asset_url(asset,tag):
    url=asset.get('browser_download_url','')
    parsed=urllib.parse.urlsplit(url)
    expected=f'/{REPOSITORY}/releases/download/{tag}/{asset["name"]}'
    if parsed.scheme!='https' or parsed.hostname!='github.com' or urllib.parse.unquote(parsed.path)!=expected or parsed.query or parsed.fragment:
        raise ValueError('Unexpected update asset URL.')
    return url


@dataclass
class Update:
    version: str
    url: str
    size: int
    sha256: str|None
    checksum_url: str|None


def find_update(release,installed):
    if release.get('draft') or release.get('prerelease'):return None
    tag=release.get('tag_name','')
    latest=version_tuple(tag)
    if latest<=version_tuple(installed):return None
    assets=release.get('assets',[])
    candidates=[asset for asset in assets if asset.get('name')==ASSET_NAME and asset.get('state')=='uploaded']
    if len(candidates)!=1:raise ValueError('The release executable is unavailable.')
    asset=candidates[0];size=asset.get('size',0)
    if not isinstance(size,int) or not 0<size<=MAX_DOWNLOAD:raise ValueError('Unexpected update size.')
    digest=asset.get('digest') or ''
    checksum=None
    if re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest):checksum=digest.split(':')[1].lower()
    checksum_asset=next((a for a in assets if a.get('name')=='SHA256SUMS.txt' and a.get('state')=='uploaded'),None)
    checksum_url=asset_url(checksum_asset,tag) if checksum_asset else None
    if not checksum and not checksum_url:raise ValueError('The update cannot be verified.')
    return Update('.'.join(map(str,latest)),asset_url(asset,tag),size,checksum,checksum_url)


def file_hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def file_version(path):
    # Spell out the DLL extension: PyInstaller otherwise finds the bundled
    # VERSION text file first on Windows' case-insensitive filesystem.
    api=ctypes.WinDLL('version.dll',use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes=[w.LPCWSTR,ctypes.POINTER(w.DWORD)]
    api.GetFileVersionInfoW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID]
    api.VerQueryValueW.argtypes=[w.LPCVOID,w.LPCWSTR,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(w.UINT)]
    ignored=w.DWORD();size=api.GetFileVersionInfoSizeW(str(path),ctypes.byref(ignored))
    if not size:raise ValueError('The update has no version information.')
    buffer=ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path),0,size,buffer):raise ValueError('Could not read the update version.')
    pointer=ctypes.c_void_p();length=w.UINT()
    if not api.VerQueryValueW(buffer,'\\',ctypes.byref(pointer),ctypes.byref(length)) or length.value<16:
        raise ValueError('Invalid executable version information.')
    signature,_,major_minor,patch_build=struct.unpack('<IIII',ctypes.string_at(pointer,16))
    if signature!=0xFEEF04BD:raise ValueError('Invalid executable version information.')
    return (major_minor>>16,major_minor&65535,patch_build>>16)


def verify_executable(path,expected_hash,version,read_version=file_version):
    if file_hash(path)!=expected_hash:raise ValueError('The update download is damaged.')
    with path.open('rb') as stream:
        header=stream.read(64)
        if len(header)!=64 or header[:2]!=b'MZ':raise ValueError('The update is not a Windows executable.')
        offset=struct.unpack_from('<I',header,60)[0]
        if not 64<=offset<=path.stat().st_size-24:raise ValueError('The update executable is incomplete.')
        stream.seek(offset);pe=stream.read(6)
        if pe[:4]!=b'PE\0\0' or pe[4:]!=b'\x64\x86':raise ValueError('The update is not a Windows x64 executable.')
    if tuple(read_version(path))!=version_tuple(version):raise ValueError('The download does not match the release version.')


def download_update(update,directory,fetch=open_url,read_version=file_version,progress=None):
    expected=update.sha256
    if expected is None:
        with fetch(update.checksum_url) as response:raw=response.read(65537)
        if len(raw)>65536:raise ValueError('Invalid update checksum file.')
        matches=re.findall(r'^([0-9a-fA-F]{64})\s+\*?AoC-bind-presets\.exe\s*$',raw.decode('utf-8-sig'),re.M)
        if len(matches)!=1:raise ValueError('The update checksum is missing.')
        expected=matches[0].lower()
    destination=directory/'candidate.exe';received=0;started=time.monotonic()
    try:
        with fetch(update.url) as response,destination.open('xb') as stream:
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                received+=len(chunk)
                if received>update.size or received>MAX_DOWNLOAD or time.monotonic()-started>180:
                    raise ValueError('Unexpected update download size or duration.')
                stream.write(chunk)
                if progress:progress(received,update.size)
            stream.flush();os.fsync(stream.fileno())
        if received!=update.size:raise ValueError('The update download is incomplete.')
        verify_executable(destination,expected,update.version,read_version)
        return destination,expected
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def wait_for_exit(pids,timeout=60):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    deadline=time.monotonic()+timeout
    for pid in pids:
        handle=kernel.OpenProcess(0x100000,False,int(pid))
        if not handle:continue
        try:
            remaining=max(0,int((deadline-time.monotonic())*1000))
            if kernel.WaitForSingleObject(handle,remaining)!=0:raise ValueError('The previous app is still running.')
        finally:kernel.CloseHandle(handle)


def launch_app(target,stage):
    return subprocess.Popen([str(target),'--updated',str(stage)],cwd=str(target.parent),close_fds=True)


def write_result(target,ok,message):
    data=target.parent/'data';data.mkdir(parents=True,exist_ok=True)
    temporary=data/'update-result.tmp'
    temporary.write_text(json.dumps({'ok':ok,'message':message}),encoding='utf-8')
    os.replace(temporary,data/'update-result.json')


def read_install_manifest(manifest_path):
    manifest_path=Path(manifest_path).resolve();stage=manifest_path.parent
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    target=Path(manifest['target']).resolve()
    if manifest.get('owner')!=OWNER or stage.parent!=target.parent or not stage.name.startswith('.aoc-update-') or manifest_path.name!='manifest.json':
        raise ValueError('Invalid update staging folder.')
    if not re.fullmatch(r'AoC-bind-presets(?: \(\d+\))?\.exe',target.name,re.I):raise ValueError('Unexpected update target.')
    return manifest,stage,target


def install_update(manifest_path,wait=wait_for_exit,launch=launch_app,read_version=file_version):
    manifest,stage,target=read_install_manifest(manifest_path)
    candidate=stage/'candidate.exe';backup=stage/'previous.exe';replaced=False
    try:
        wait(manifest['pids'])
        verify_executable(candidate,manifest['sha256'],manifest['version'],read_version)
        if file_hash(target)!=manifest['original_sha256']:
            write_result(target,False,'The app changed during the update. Please open it again.')
            launch(target,stage);return False
        shutil.copy2(target,backup)
        deadline=time.monotonic()+15
        while True:
            try:os.replace(candidate,target);break
            except PermissionError:
                if time.monotonic()>=deadline:raise ValueError('The app executable is still locked.')
                time.sleep(.25)
        replaced=True
        write_result(target,True,'Updated to v'+manifest['version']+'.')
        launch(target,stage)
        return True
    except Exception:
        if replaced and backup.exists():os.replace(backup,target)
        write_result(target,False,'The app could not update. Your previous version and presets have been kept.')
        launch(target,stage)
        return False


def prepare_install(target,stage,version,digest,position=None):
    if not re.fullmatch(r'AoC-bind-presets(?: \(\d+\))?\.exe',target.name,re.I):
        raise ValueError('Keep the standard app filename to install updates automatically.')
    if stage.resolve().parent!=target.resolve().parent:
        raise ValueError('The app and update must be in the same folder.')
    manifest={'owner':OWNER,'target':str(target.resolve()),'version':version,'sha256':digest,
              'original_sha256':file_hash(target),'pids':[os.getpid(),os.getppid()],
              'handoff':1,'position':position}
    (stage/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    helper=stage/'update-helper.exe';shutil.copy2(target,helper)
    return subprocess.Popen([str(helper),'--install-update',str(stage/'manifest.json')],cwd=str(stage),creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)


def cleanup_stage(stage,base):
    stage=Path(stage).resolve();base=Path(base).resolve()
    if stage.parent!=base or not stage.name.startswith('.aoc-update-'):return True
    manifest=stage/'manifest.json'
    try:
        payload=json.loads(manifest.read_text())
        if payload.get('owner')!=OWNER:return True
        if payload.get('handoff') and not (stage/'helper-done.json').exists():return False
        for name in ('candidate.exe','previous.exe','update-helper.exe','helper-ready.json','manager-ready.json','helper-done.json','cancel-update.json','manifest.json'):
            (stage/name).unlink(missing_ok=True)
        stage.rmdir()
        return True
    except FileNotFoundError:return not stage.exists()
    except (OSError,ValueError):return False


class UpdateController:
    def __init__(self,app):
        self.app=app;self.root=app.root;self.results=queue.SimpleQueue();self.pending=None;self.draft_restored=False
        self.dialog=None;self.progress_state=None

    def idle(self):
        app=self.app
        if app.busy or app.capture_state or self.root.grab_current() is not None:return False
        if any(child is not self.dialog and child.winfo_viewable() and not child.overrideredirect() for child in self.root.winfo_children() if child.winfo_class()=='Toplevel'):return False
        if os.name=='nt':
            user=ctypes.WinDLL('user32')
            user.GetAncestor.argtypes=[w.HWND,w.UINT];user.GetAncestor.restype=w.HWND
            user.IsWindowEnabled.argtypes=[w.HWND]
            if not user.IsWindowEnabled(user.GetAncestor(self.root.winfo_id(),2)):return False
            if any(user.GetAsyncKeyState(key)&0x8000 for key in (1,2,4,5,6,16,17,18)):return False
        return True

    def start(self,check=True,cleanup=None):
        self.record_status('checking' if check else 'check-disabled')
        if cleanup:self.root.after(100,lambda:self.confirm_open(cleanup))
        self.root.after(150,self.restore_draft)
        if not check:return
        def worker():
            stage=None
            try:
                installed=current_version();release=latest_release()
                self.record_status('checked',installed=installed,latest=release.get('tag_name'))
                update=find_update(release,installed)
                if update is None:self.results.put(('done',None));return
                self.results.put(('downloading',update.version))
                stage=Path(tempfile.mkdtemp(prefix='.aoc-update-',dir=self.app.data.parent))
                _,digest=download_update(update,stage,progress=lambda done,total:self.results.put(('progress',(done,total))))
                self.results.put(('ready',(stage,update.version,digest)))
            except Exception as exc:
                if stage:
                    try:
                        (stage/'candidate.exe').unlink(missing_ok=True)
                        stage.rmdir()
                    except OSError:pass
                self.results.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
        self.root.after(150,self.poll)

    def confirm_open(self,stage):
        if not self.root.winfo_viewable() or self.app.busy or not self.draft_restored:
            self.root.after(100,lambda:self.confirm_open(stage));return
        try:
            manifest,folder,target=read_install_manifest(Path(stage)/'manifest.json')
            if target.parent!=self.app.data.parent.resolve():return
            if manifest.get('handoff'):
                (folder/'manager-ready.json').write_text(json.dumps({'owner':OWNER}),encoding='utf-8')
        except (OSError,ValueError,KeyError):return
        self.root.after(2500,lambda:self.cleanup_update(stage))

    def cleanup_update(self,stage,attempts=0):
        if not cleanup_stage(stage,self.app.data.parent) and attempts<120:
            self.root.after(500,lambda:self.cleanup_update(stage,attempts+1))

    def finish_handoff(self):
        stage,process,deadline=self.handoff
        ready=stage/'helper-ready.json'
        try:
            if ready.exists() and json.loads(ready.read_text(encoding='utf-8')).get('owner')==OWNER:
                self.root.destroy();return
            if process.poll() is not None or time.monotonic()>deadline:
                raise ValueError('The update window could not open. You can keep using the app.')
        except (OSError,ValueError) as exc:
            # Release any late-starting helper without closing the manager.
            try:(stage/'cancel-update.json').write_text(json.dumps({'owner':OWNER}),encoding='utf-8')
            except OSError:pass
            (self.app.data/'update-draft.json').unlink(missing_ok=True)
            self.app.busy=False;self.report_error(str(exc));return
        self.root.after(50,self.finish_handoff)

    def record_status(self,phase,**details):
        try:
            self.app.data.mkdir(parents=True,exist_ok=True)
            (self.app.data/'update-status.json').write_text(json.dumps({'phase':phase,**details}),encoding='utf-8')
        except OSError:pass

    def show_progress(self):
        if not self.progress_state or not self.idle():return
        if self.dialog is None:
            dialog=tk.Toplevel(self.root);dialog.withdraw();dialog.title('Updating AoC-bind-presets')
            dialog.transient(self.root);dialog.resizable(False,False);dialog.configure(bg='#0d1217')
            dialog.protocol('WM_DELETE_WINDOW',lambda:None)
            panel=ttk.Frame(dialog,padding=24);panel.pack(fill='both',expand=True)
            self.update_heading=ttk.Label(panel,style='Heading.TLabel');self.update_heading.pack(anchor='w',pady=(0,14))
            self.progress_bar=ttk.Progressbar(panel,length=380,maximum=100,mode='determinate',style='Update.Horizontal.TProgressbar')
            ttk.Style(self.root).configure('Update.Horizontal.TProgressbar',background='#bfa140',troughcolor='#211c13',bordercolor='#8e7135',lightcolor='#e8ca65',darkcolor='#6e541b',thickness=18)
            self.progress_bar.pack(fill='x')
            self.update_detail=ttk.Label(panel);self.update_detail.pack(anchor='w',pady=(10,0))
            self.root.update_idletasks();dialog.update_idletasks()
            x=self.root.winfo_rootx()+(self.root.winfo_width()-dialog.winfo_reqwidth())//2
            y=self.root.winfo_rooty()+(self.root.winfo_height()-dialog.winfo_reqheight())//2
            dialog.geometry(f'+{max(0,x)}+{max(0,y)}');dialog.deiconify()
            if os.name=='nt':
                user=ctypes.WinDLL('user32')
                user.GetAncestor.argtypes=[w.HWND,w.UINT];user.GetAncestor.restype=w.HWND
                user.SetWindowPos.argtypes=[w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT]
                user.SetWindowPos(user.GetAncestor(dialog.winfo_id(),2),None,x,y,0,0,0x15)
            self.dialog=dialog
        version,percent,detail=self.progress_state
        self.update_heading.configure(text='Updating to v'+version+'…')
        self.progress_bar['value']=percent
        self.update_detail.configure(text=detail)

    def close_progress(self):
        if self.dialog is not None:self.dialog.destroy();self.dialog=None
        self.progress_state=None

    def report_error(self,message):
        self.record_status('error',message=message)
        self.close_progress()
        self.app.status.set('Could not update automatically. '+message)
        try:
            self.app.data.mkdir(parents=True,exist_ok=True)
            (self.app.data/'update-error.log').write_text(time.strftime('%Y-%m-%d %H:%M:%S')+' '+message+'\n',encoding='utf-8')
        except OSError:pass

    def restore_draft(self):
        if not self.idle():self.root.after(150,self.restore_draft);return
        path=self.app.data/'update-draft.json'
        if path.exists():
            try:
                draft=json.loads(path.read_text(encoding='utf-8'))
                self.app.fill(draft['bindings'],draft.get('priorities'))
                name=draft.get('name','')
                self.app.name.set(name if name in self.app.store['presets'] else '')
                self.app.highlight_selection();path.unlink()
            except (OSError,ValueError,KeyError):self.app.status.set('Could not restore the editing draft. Your saved presets are unchanged.')
        result=self.app.data/'update-result.json'
        if result.exists():
            try:self.app.status.set(json.loads(result.read_text(encoding='utf-8'))['message']);result.unlink()
            except (OSError,ValueError,KeyError):pass
        self.draft_restored=True

    def poll(self):
        try:
            while True:
                kind,value=self.results.get_nowait()
                if kind=='done':return
                if kind=='error':
                    self.report_error(value or 'Please try again later.')
                    return
                if kind=='downloading':
                    self.progress_state=(value,0,'Downloading… 0%')
                    self.app.status.set('Downloading update v'+value+'…')
                if kind=='progress' and self.progress_state:
                    done,total=value;percent=min(100,int(done*100/total))
                    self.progress_state=(self.progress_state[0],percent,'Verifying download…' if done==total else f'Downloading… {percent}%')
                if kind=='ready':
                    self.pending=value
                    self.progress_state=(value[1],100,'Download verified. Preparing to restart…')
        except queue.Empty:pass
        self.show_progress()
        if self.pending and self.draft_restored and self.idle():
            stage,version,digest=self.pending
            try:
                draft={'name':self.app.name.get(),'bindings':self.app.form_values(),'priorities':self.app.edit_order}
                self.app.data.mkdir(parents=True,exist_ok=True)
                path=self.app.data/'update-draft.json';temporary=path.with_suffix('.tmp')
                temporary.write_text(json.dumps(draft),encoding='utf-8');os.replace(temporary,path)
                self.app.busy=True;self.app.status.set('Updating to v'+version+'… Restarting the app.')
                position=[self.dialog.winfo_x(),self.dialog.winfo_y()] if self.dialog is not None else None
                if self.dialog is not None:self.update_detail.configure(text='Restarting the app…')
                process=prepare_install(Path(sys.executable),stage,version,digest,position=position)
                self.handoff=(stage,process,time.monotonic()+30)
                self.root.after(50,self.finish_handoff)
                return
            except Exception as exc:
                (self.app.data/'update-draft.json').unlink(missing_ok=True)
                self.app.busy=False;self.report_error(str(exc))
                return
        self.root.after(150,self.poll)
