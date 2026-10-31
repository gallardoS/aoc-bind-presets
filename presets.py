"""Portable AoC keybinding preset manager. Standard library only."""
from __future__ import annotations

import csv
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import queue
import urllib.request
import urllib.error
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from restart_game import running_game, apply_with_restart
from wc3_theme import install as install_wc3_theme
from binding_schema import Schema, patch_document, label_for

BASE = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DEFAULT_SETTINGS = Path(os.environ.get('APPDATA', str(Path.home()))) / 'arena-of-champions' / 'settings.ron'
RESOURCES = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
CLASSES = ['Warrior', 'Mage', 'Priest', 'Rogue', 'Hunter', 'Paladin', 'Warlock', 'Druid', 'Shaman']
SPECIALIZATIONS = {
    'Warrior': [('Arms', 'ability_warrior_savageblow'), ('Fury', 'ability_warrior_innerrage'), ('Protection', 'ability_warrior_defensivestance')],
    'Mage': [('Arcane', 'spell_holy_magicalsentry'), ('Fire', 'spell_fire_firebolt02'), ('Frost', 'spell_frost_frostbolt02')],
    'Priest': [('Discipline', 'spell_holy_powerwordshield'), ('Holy', 'spell_holy_holybolt'), ('Shadow', 'spell_shadow_shadowwordpain')],
    'Rogue': [('Assassination', 'ability_rogue_eviscerate'), ('Combat', 'ability_backstab'), ('Subtlety', 'ability_stealth')],
    'Hunter': [('Beast Mastery', 'ability_hunter_beasttaming'), ('Marksmanship', 'ability_marksmanship'), ('Survival', 'ability_hunter_swiftstrike')],
    'Paladin': [('Holy', 'spell_holy_holybolt'), ('Protection', 'spell_holy_devotionaura'), ('Retribution', 'spell_holy_auraoflight')],
    'Warlock': [('Affliction', 'spell_shadow_deathcoil'), ('Demonology', 'spell_shadow_metamorphosis'), ('Destruction', 'spell_shadow_rainoffire')],
    'Druid': [('Balance', 'spell_nature_starfall'), ('Restoration', 'spell_nature_healingtouch'), ('Feral', 'ability_druid_catform')],
    'Shaman': [('Elemental', 'spell_nature_lightning'), ('Enhancement', 'spell_nature_lightningshield'), ('Restoration', 'spell_nature_magicimmunity')],
}
SPEC_PRESETS = [f'{name} - {spec}' for name in CLASSES for spec, _ in SPECIALIZATIONS[name]]


def add_specialization_presets(store):
    """Seed missing specializations from their class without changing saved presets."""
    if not store['presets']: return False
    changed = False
    priorities = store.setdefault('binding_priorities', {})
    for name in CLASSES:
        baseline = store['presets'].get(name, store['presets'].get('Original'))
        if baseline is None: continue
        for spec, _ in SPECIALIZATIONS[name]:
            key = f'{name} - {spec}'
            if key not in store['presets']:
                store['presets'][key] = dict(baseline)
                priorities[key] = dict(priorities.get(name, {}))
                changed = True
    return changed


# These are display labels and category hints, not a required binding schema.
# Every editable row is discovered from the selected settings file.
ACTION_FIELDS = [(f'action_{i+1}', f'Action {i+1}') for i in range(8)]
BESIDE_FIELDS = [
    ('stealth', 'Class ability 1'), ('class_2', 'Class ability 2'),
    ('class_3', 'Class ability 3'), ('trinket', 'Utility 1'), ('utility_2', 'Utility 2')]
MOVEMENT_FIELDS = [('forward','Move forward'),('back','Move back'),('left','Move left'),('right','Move right'),('jump','Jump')]
TARGET_FIELDS = [('next_target','Target nearest enemy'),('focus','Set focus'),('focus_mouseover','Focus mouseover'),('target_focus','Target focus'),('target_self','Target self'),('target_teammate_1','Target teammate 1'),('target_teammate_2','Target teammate 2'),('inspect','Inspect target')]
CAMERA_FIELDS = [('zoom_in','Zoom in'),('zoom_out','Zoom out')]
INTERFACE_FIELDS = [('sheet','Spells & Talents')]
FIELDS = ACTION_FIELDS + BESIDE_FIELDS + MOVEMENT_FIELDS + TARGET_FIELDS + CAMERA_FIELDS + INTERFACE_FIELDS
COMBAT_KEYS = {key for key,_ in ACTION_FIELDS + BESIDE_FIELDS}
FIELD_KEYS = {key for key,_ in FIELDS}
SINGLE = [key for key,_ in FIELDS if not key.startswith('action_')]
BINDING_COLUMNS = [
    [('Movement',MOVEMENT_FIELDS),('Targeting',TARGET_FIELDS),('Camera',CAMERA_FIELDS)],
    [('Action Bar',ACTION_FIELDS),('Beside the Bar',BESIDE_FIELDS),('Interface',INTERFACE_FIELDS)],
]
TOKEN = r'"([^"\\\r\n]*)"'


def user_path(value):
    # Windows paths cannot contain double quotes. File dialogs can return
    # pasted paths with an unmatched opening quote as literal filename text.
    text = str(value).strip().replace('\\"','"').strip('"').strip()
    if len(text)>1 and text[0]==text[-1]=="'": text=text[1:-1]
    return Path(text)


def center_dialog(dialog, parent):
    parent.update_idletasks()
    dialog.update_idletasks()
    width,height=dialog.winfo_reqwidth(),dialog.winfo_reqheight()
    x=parent.winfo_rootx()+(parent.winfo_width()-width)//2
    y=parent.winfo_rooty()+(parent.winfo_height()-height)//2
    dialog.geometry(f'{width}x{height}+{max(0,x)}+{max(0,y)}')
    dialog.deiconify()
    if os.name=='nt':
        # Win32 coordinates also handle monitors to the left of the primary
        # screen, where Tk's negative geometry offsets have another meaning.
        from ctypes import wintypes as w
        class MonitorInfo(ctypes.Structure):
            _fields_=[('cbSize',w.DWORD),('rcMonitor',w.RECT),('rcWork',w.RECT),('dwFlags',w.DWORD)]
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.GetAncestor.argtypes=[w.HWND,w.UINT];user.GetAncestor.restype=w.HWND
        user.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
        user.MonitorFromWindow.argtypes=[w.HWND,w.DWORD];user.MonitorFromWindow.restype=w.HANDLE
        user.GetMonitorInfoW.argtypes=[w.HANDLE,ctypes.POINTER(MonitorInfo)]
        user.SetWindowPos.argtypes=[w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT]
        owner=user.GetAncestor(parent.winfo_id(),2)
        popup=user.GetAncestor(dialog.winfo_id(),2)
        owner_rect,popup_rect=w.RECT(),w.RECT()
        if user.GetWindowRect(owner,ctypes.byref(owner_rect)) and user.GetWindowRect(popup,ctypes.byref(popup_rect)):
            width,height=popup_rect.right-popup_rect.left,popup_rect.bottom-popup_rect.top
            x=(owner_rect.left+owner_rect.right-width)//2
            y=(owner_rect.top+owner_rect.bottom-height)//2
            info=MonitorInfo();info.cbSize=ctypes.sizeof(info)
            if user.GetMonitorInfoW(user.MonitorFromWindow(owner,2),ctypes.byref(info)):
                x=max(info.rcWork.left,min(x,info.rcWork.right-width))
                y=max(info.rcWork.top,min(y,info.rcWork.bottom-height))
            user.SetWindowPos(popup,None,x,y,0,0,0x15)


def key_section(text):
    root=Schema(text).root
    return root.start+1,root.close


def read_bindings(text):
    return Schema(text).values()


def binding_label(key):
    return dict(FIELDS).get(key,label_for(key))


def binding_columns(keys):
    remaining=set(keys); columns=[[],[]]
    for col,groups in enumerate(BINDING_COLUMNS):
        for title,known in groups:
            fields=[(key,label) for key,label in known if key in remaining]
            if title=='Targeting':
                existing={key for key,_ in fields}
                added=[(key,binding_label(key)) for key in sorted(remaining) if key.startswith('target_') and key not in existing]
                fields=[item for item in fields if item[0]!='inspect']+added+[item for item in fields if item[0]=='inspect']
            if title=='Action Bar':
                fields=[(key,binding_label(key)) for key in sorted(remaining,key=lambda k:int(k.split('_')[1]) if re.fullmatch(r'action_\d+',k) else -1) if re.fullmatch(r'action_\d+',key)]
            if fields:
                columns[col].append((title,fields));remaining.difference_update(key for key,_ in fields)
    if remaining:columns[1].append(('Other',[(key,binding_label(key)) for key in keys if key in remaining]))
    return columns


def canonical(value):
    if not value.strip(): return ''
    parts = value.strip().split('-')
    modifiers = parts[:-1]
    if len(set(modifiers)) != len(modifiers) or any(x not in ('Ctrl', 'Alt', 'Shift') for x in modifiers):
        raise ValueError(f'Invalid combination: {value}')
    key = parts[-1]
    if not re.fullmatch(r'(?:Key[A-Z]|Digit[0-9]|F(?:[1-9]|1[0-9]|2[0-4])|Space|Tab|CapsLock|Enter|Escape|Backspace|Delete|Insert|Home|End|PageUp|PageDown|Arrow(?:Up|Down|Left|Right)|Backquote|Minus|Equal|BracketLeft|BracketRight|Backslash|Semicolon|Quote|Comma|Period|Slash|Mouse(?:Left|Right|Middle|[45]|WheelUp|WheelDown)|Numpad(?:[0-9]|Add|Subtract|Multiply|Divide|Decimal|Enter))', key):
        raise ValueError(f'Unknown key: {value}\nClick a binding field to capture a valid key or mouse button.')
    return '-'.join([m for m in ('Ctrl', 'Alt', 'Shift') if m in modifiers] + [key])


def validate(values):
    if not isinstance(values,dict) or not values:
        raise ValueError('Read the game settings before editing a preset.')
    if any(not isinstance(key,str) or not re.fullmatch(r'[A-Za-z_]\w*(?:(?:\.[A-Za-z_]\w*)|(?:\[\d+\]))*',key) for key in values):
        raise ValueError('The preset contains an invalid binding name.')
    if not all(isinstance(value,str) for value in values.values()):raise ValueError('Bindings must be strings.')
    return {key:canonical(value) for key,value in values.items()}


def complete_bindings(values,baseline):
    values=validate(values);baseline=validate(baseline)
    return {key:values.get(key,value) for key,value in baseline.items()}


def validate_for_game(values):
    values=validate(values)
    empty=[binding_label(key) for key,value in values.items() if not value]
    if empty:raise ValueError('Arena of Champions cannot load empty bindings. Assign a key before applying:\n\n'+'\n'.join(empty))
    return values


def patch_settings(text,values,template=None):
    return patch_document(text,validate(values),template)


def bindings_match(text,values):
    actual=read_bindings(text)
    return all(actual.get(key)==value for key,value in values.items())


def conflicts(text,values,template=None):
    actual=read_bindings(patch_settings(text,values,template))
    seen={};issues=[]
    for key,value in actual.items():
        if not value:continue
        if value in seen:issues.append(f'{binding_label(seen[value])} and {binding_label(key)}: {value}')
        else:seen[value]=key
    return issues


def game_running():
    result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10)
    if result.returncode: raise RuntimeError('Could not check whether the game is running.')
    rows = csv.reader(result.stdout.decode('utf-8', errors='replace').splitlines())
    return any(row and (row[0].lower() in ('arenaofchampions.exe', 'aoc-client.exe', 'aoc_client.exe')
                       or row[0].lower().startswith('arenaofchampions')) for row in rows)


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def apply_bindings(path, values, backup_dir, check_game=game_running, template=None):
    values = validate_for_game(values)
    if check_game(): raise ValueError('Close Arena of Champions before applying a preset.')
    before = path.read_bytes()
    text = before.decode('utf-8-sig')
    issues = conflicts(text, values,template=template)
    if issues: raise ValueError('Duplicate bindings. Resolve these conflicts before applying:\n\n' + '\n'.join(issues))
    after = patch_settings(text, values,template=template).encode('utf-8')
    if before.startswith(b'\xef\xbb\xbf'): after = b'\xef\xbb\xbf' + after
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = backup_dir / (stamp + '-settings.ron')
    atomic_write(backup, before)
    if path.read_bytes() != before: raise ValueError('The settings file changed during the operation. Please try again.')
    if check_game(): raise ValueError('The game has started. Close it before applying.')
    atomic_write(path, after)
    return backup


def account_api(method, payload, token):
    request = urllib.request.Request(
        'https://arenaofchampions.live/api/settings',
        data=json.dumps(payload).encode('utf-8') if payload is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                 'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(2_000_000)
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError('Your game session has expired. Sign in to the game, close it, then apply again.') from None
        raise ValueError(f'The game server rejected the settings request (HTTP {exc.code}).') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError('Could not reach the game server. Check your connection and try again.') from None


def session_token(path):
    session_path = path.parent / 'session.ron'
    if not session_path.exists(): session_path = path.parent / 'guest.ron'
    if not session_path.exists(): return None
    session = session_path.read_text(encoding='utf-8-sig')
    match = re.search(r'\btoken\s*:\s*"([^"\r\n]+)"',session)
    if not match: raise ValueError('The saved game session is not recognized. Sign in to the game again.')
    return match.group(1)


def read_game_snapshot(path, api=None):
    local = path.read_bytes()
    bindings = validate(read_bindings(local.decode('utf-8-sig')))
    snapshot = {'local':local,'document':local.decode('utf-8-sig'),'bindings':bindings,'account':None,'source':'game','notice':''}
    try:
        token = session_token(path)
        if token:
            response = (api or account_api)('GET',None,token)
            remote = response.get('settings') if isinstance(response,dict) else None
            if isinstance(remote,str):
                combined=patch_settings(snapshot['document'],read_bindings(remote),template=remote)
                snapshot.update(document=combined,bindings=validate(read_bindings(combined)),account=remote,source='account')
    except (ValueError,OSError):
        snapshot['notice'] = 'Account settings could not be checked. Loaded the local game settings.'
    if any(not value for value in snapshot['bindings'].values()):
        snapshot['notice'] = 'Your saved settings contain empty bindings the game cannot load. Assign keys before applying.'
    return snapshot


def apply_synced_bindings(path, values, backup_dir, check_game=game_running, api=account_api):
    """Patch only our bindings in both existing documents, keeping tokens in memory."""
    values = validate_for_game(values)
    if check_game(): raise ValueError('Close Arena of Champions before applying a preset.')
    before = path.read_bytes()
    local = before.decode('utf-8-sig')
    token = session_token(path)
    if not token:
        raise ValueError('No game session found. Sign in to the game, close it, then apply again.')
    response = api('GET', None, token)
    remote = response.get('settings') if isinstance(response, dict) else None
    if not isinstance(remote, str):
        raise ValueError('No saved account settings found. Change a setting in the game, close it, then apply again.')
    combined=patch_settings(local,read_bindings(remote),template=remote)
    issues=conflicts(local,values,template=combined)
    if issues:raise ValueError('Duplicate bindings:\n\n'+'\n'.join(issues))
    issues = conflicts(remote, values,template=combined)
    if issues: raise ValueError('These bindings conflict with your account settings:\n\n' + '\n'.join(issues))
    updated = patch_settings(remote, values,template=combined)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    atomic_write(backup_dir / (stamp + '-account-settings.json'), json.dumps({'settings': remote}, indent=2).encode())
    # Keep a local backup before the remote operation as well, even if the
    # subsequent local write fails or the server cannot confirm the change.
    atomic_write(backup_dir / (stamp + '-settings.ron'), before)
    if check_game(): raise ValueError('The game has started. Close it before applying.')
    if path.read_bytes() != before: raise ValueError('The local settings changed. Please try again.')
    api('PUT', {'settings': updated}, token)
    try:
        verified = api('GET', None, token)
        saved = verified.get('settings') if isinstance(verified, dict) else None
        if not isinstance(saved, str) or not bindings_match(saved,values):
            raise ValueError('The server returned different bindings.')
    except Exception:
        raise ValueError('The account update was sent, but could not be verified. The local file was not changed. Try applying again.') from None
    try:
        if path.read_bytes() != before: raise ValueError('The local settings changed during synchronization.')
        backup = apply_bindings(path, values, backup_dir, check_game,template=combined)
    except Exception as exc:
        raise ValueError(f'Account bindings were updated, but the local file could not be updated: {exc}') from None
    if not bindings_match(path.read_bytes().decode('utf-8-sig'),values):
        raise ValueError('The account was updated, but local verification failed. Close the game and apply again.')
    return backup


class App:
    def __init__(self, root):
        self.root = root
        self.data = BASE / 'data'
        self.store_path = self.data / 'presets.json'
        self.store = {'version': 3, 'settings_path': str(DEFAULT_SETTINGS), 'presets': {}}
        if self.store_path.exists():
            saved = json.loads(self.store_path.read_text(encoding='utf-8'))
            self.store = {key: saved[key] for key in ('version', 'settings_path', 'presets', 'binding_priorities') if key in saved}
            if self.store.get('version') not in (1,2,3): raise ValueError('Unsupported presets version.')
        if add_specialization_presets(self.store):
            if self.store_path.exists():
                stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                atomic_write(self.data/'backups'/(stamp+'-presets.json'), self.store_path.read_bytes())
            atomic_write(self.store_path, json.dumps(self.store, indent=2, ensure_ascii=False).encode('utf-8'))
        self.active_class = 'Warrior'
        self.last_specialization = {}
        self.path = tk.StringVar(value=self.store.get('settings_path', str(DEFAULT_SETTINGS)))
        self.document=None
        self.schema=None
        if self.settings_path().exists():
            try:
                self.document=self.settings_path().read_bytes().decode('utf-8-sig')
                self.schema=Schema(self.document)
            except (OSError,ValueError):self.document=None;self.schema=None
        self.fields=[(key,binding_label(key)) for key in self.schema.bindings] if self.schema else []
        self.field_keys={key for key,_ in self.fields}
        self.name = tk.StringVar()
        self.values = {key: tk.StringVar() for key, _ in self.fields}
        self.displays = {key: tk.StringVar() for key, _ in self.fields}
        self.binding_entries = {}
        self.binding_labels = {}
        self.binding_frames = {}
        self.clear_buttons = {}
        self.edit_order = {key: 0 for key, _ in self.fields}
        self.edit_counter = 0
        self.loading_bindings = False
        self.conflict_fields = set()
        self.capture_state = None
        self.busy = False
        self.restart_dialog = None
        for key, value in self.values.items():
            value.trace_add('write', lambda *args, k=key: self.sync_display(k))
        self.status = tk.StringVar(value='Select a preset.')
        self.selected_label = tk.StringVar(value='Current bindings')
        self.store.setdefault('binding_priorities', {})
        self.icons = {name: tk.PhotoImage(file=str(RESOURCES / 'icons' / (name.lower() + '.png'))).zoom(3, 3).subsample(4, 4) for name in CLASSES}
        self.spec_icons = {icon: tk.PhotoImage(file=str(RESOURCES/'icons'/'specializations'/(icon+'.png'))).zoom(3,3).subsample(4,4)
                           for specs in SPECIALIZATIONS.values() for name,icon in specs}
        self.preset_buttons = {}
        self.spec_buttons = {}
        root.title('AoC-bind-presets')
        root.iconbitmap(default=str(RESOURCES / 'icons' / 'app.ico'))
        window_width=min(1180,root.winfo_screenwidth()-60)
        window_height=min(950,root.winfo_screenheight()-100)
        root.geometry(f'{window_width}x{window_height}')
        root.minsize(min(1080,window_width),min(780,window_height))
        install_wc3_theme(root,RESOURCES)
        outer = ttk.Frame(root,style='Window.TFrame',padding=(34,30,34,28))
        outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='AoC-bind-presets',style='Title.TLabel',anchor='center').pack(fill='x',pady=(0,14))
        location = ttk.Frame(outer); location.pack(fill='x')
        ttk.Entry(location, textvariable=self.path).pack(side='left', fill='x', expand=True)
        ttk.Button(location, text='Browse settings.ron', command=lambda: self.safe(self.browse)).pack(side='left', padx=(8, 0))
        body = ttk.Frame(outer); body.pack(fill='both', expand=True, pady=(18, 0))
        sidebar = ttk.Frame(body); sidebar.pack(side='left', fill='y', padx=(0, 20))
        self.preset_canvas = tk.Canvas(sidebar, width=76, background='#0d1217', highlightthickness=0)
        self.preset_canvas.pack(side='left', fill='y')
        scrollbar = ttk.Scrollbar(sidebar, orient='vertical', command=self.preset_canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.preset_canvas.configure(yscrollcommand=scrollbar.set)
        self.preset_list = ttk.Frame(self.preset_canvas)
        self.preset_canvas.create_window((0, 0), window=self.preset_list, anchor='nw', width=76)
        self.preset_list.bind('<Configure>', lambda e: self.preset_canvas.configure(scrollregion=self.preset_canvas.bbox('all')))
        self.preset_canvas.bind('<MouseWheel>', lambda e: self.preset_canvas.yview_scroll(-int(e.delta / 120), 'units'))
        content = ttk.Frame(body); content.pack(side='left', fill='both', expand=True)
        presets = ttk.Frame(content); presets.pack(fill='x', pady=(0, 18))
        ttk.Label(presets, textvariable=self.selected_label, style='Heading.TLabel').pack(side='left', fill='x', expand=True)
        for label, command in [('Save to…', self.save_as), ('Save', self.update), ('Reset', self.delete)]:
            ttk.Button(presets, text=label, command=lambda f=command: self.safe(f)).pack(side='left', padx=(8, 0))
        self.spec_bar = ttk.Frame(content)
        self.spec_bar.pack(fill='x', pady=(0,14))
        controls = ttk.Frame(content); controls.pack(fill='both',expand=True)
        self.bindings_canvas = tk.Canvas(controls,bg='#0d1217',highlightthickness=0)
        self.bindings_canvas.pack(side='left',fill='both',expand=True)
        control_scrollbar = ttk.Scrollbar(controls,orient='vertical',command=self.bindings_canvas.yview)
        control_scrollbar.pack(side='right',fill='y')
        self.bindings_canvas.configure(yscrollcommand=control_scrollbar.set)
        self.bindings_panel = ttk.Frame(self.bindings_canvas)
        panel_id = self.bindings_canvas.create_window((0,0),window=self.bindings_panel,anchor='nw')
        self.bindings_canvas.bind('<Configure>',lambda e: self.bindings_canvas.itemconfigure(panel_id,width=e.width))
        self.bindings_panel.bind('<Configure>',lambda e: self.bindings_canvas.configure(scrollregion=self.bindings_canvas.bbox('all')))
        self.render_binding_fields()
        ttk.Label(outer, textvariable=self.status, wraplength=850, foreground='#b8a780').pack(anchor='w', pady=(6, 12))
        bottom = ttk.Frame(outer); bottom.pack(fill='x')
        for label, command in [('Read from game', self.read_current), ('Restore bindings…', self.restore), ('Import', self.import_preset), ('Export', self.export_preset)]:
            ttk.Button(bottom, text=label, command=lambda f=command: self.safe(f)).pack(side='left', padx=(0, 6))
        ttk.Button(bottom, text='Apply preset', command=lambda: self.safe(self.apply)).pack(side='right')
        self.refresh()
        root.bind('<FocusOut>', self.capture_focus_out, add='+')
        root.bind('<Destroy>', lambda event: self.cancel_capture() if event.widget == root else None, add='+')
        root.protocol('WM_DELETE_WINDOW', self.close_manager)
        if self.field_keys and self.store['presets'] and not self.needs_upgrade():
            self.select('Warrior')
        else:
            self.read_current()

    def render_binding_fields(self):
        for child in self.bindings_panel.winfo_children():child.destroy()
        self.binding_entries.clear();self.binding_labels.clear();self.binding_frames.clear();self.clear_buttons.clear()
        for col,groups in enumerate(binding_columns([key for key,_ in self.fields])):
            column=ttk.Frame(self.bindings_panel,padding=(0 if col==0 else 18,0,12,0))
            column.grid(row=0,column=col,sticky='new')
            self.bindings_panel.columnconfigure(col,weight=1,uniform='bindings')
            for title,fields in groups:
                box=ttk.Frame(column);box.pack(fill='x',pady=(0,14))
                heading=ttk.Frame(box);heading.grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,7))
                ttk.Label(heading,text=title,style='Heading.TLabel').pack(anchor='w')
                ttk.Separator(heading,orient='horizontal').pack(fill='x',pady=(4,0))
                for row,(key,label) in enumerate(fields,1):self.build_binding_row(box,row,key,label)
                box.columnconfigure(0,minsize=150);box.columnconfigure(1,weight=1)
        def enable_scroll(widget):
            widget.bind('<MouseWheel>',self.scroll_bindings,add='+')
            for child in widget.winfo_children():enable_scroll(child)
        enable_scroll(self.bindings_canvas)

    def update_schema(self,document):
        schema=Schema(document)
        keys=set(schema.bindings)
        self.document=document;self.schema=schema
        if keys==self.field_keys:return
        self.cancel_capture()
        previous={key:value.get() for key,value in self.values.items()}
        self.fields=[(key,binding_label(key)) for key in schema.bindings]
        self.field_keys=keys
        self.values={key:tk.StringVar(value=previous.get(key,'')) for key,_ in self.fields}
        self.displays={key:tk.StringVar(value=value.get()) for key,value in self.values.items()}
        self.edit_order={key:self.edit_order.get(key,0) for key in keys}
        for key,value in self.values.items():value.trace_add('write',lambda *args,k=key:self.sync_display(k))
        self.render_binding_fields()

    def scroll_bindings(self,event):
        self.bindings_canvas.yview_scroll(-int(event.delta/120),'units')
        return 'break'

    def build_binding_row(self,box,row,key,label):
        name_label = ttk.Label(box, text=label, cursor='hand2')
        name_label.grid(row=row, column=0, sticky='w', padx=(0, 10), pady=2)
        field = tk.Frame(box,bg='#211c13',highlightbackground='#8e7135',highlightthickness=1)
        field.grid(row=row, column=1, sticky='ew', pady=2)
        entry = ttk.Entry(field, textvariable=self.displays[key], width=24, state='readonly', cursor='hand2', style='Binding.TEntry',font=('Georgia',10,'bold'))
        entry.pack(side='left',fill='x',expand=True)
        clear = tk.Canvas(field,width=26,height=24,bg='#211c13',highlightthickness=0,cursor='hand2',takefocus=True)
        clear.pack(side='right',padx=(1,3))
        clear.create_line(7,8,19,8,fill='#b8a780',width=1.5,tags='bin')
        clear.create_line(10,7,10,5,16,5,16,7,fill='#b8a780',width=1.5,tags='bin')
        clear.create_line(8,10,9,20,17,20,18,10,fill='#b8a780',width=1.5,tags='bin')
        clear.create_line(11,11,11,17,fill='#b8a780',width=1,tags='bin')
        clear.create_line(15,11,15,17,fill='#b8a780',width=1,tags='bin')
        self.add_tooltip(clear,'Clear binding')
        clear.bind('<ButtonPress-1>',lambda event,k=key: self.clear_binding(k),add='+')
        clear.bind('<Return>',lambda event,k=key: self.clear_binding(k))
        clear.bind('<space>',lambda event,k=key: self.clear_binding(k))
        self.binding_frames[key] = field
        self.clear_buttons[key] = clear
        self.binding_labels[key] = name_label
        self.binding_entries[key] = entry
        for widget in (name_label, entry):
            widget.bind('<ButtonPress-1>', lambda event, k=key: self.capture(k))
        entry.bind('<Return>', lambda event, k=key: self.capture(k))

    def close_manager(self):
        if self.busy:
            self.status.set('Please wait until the current operation finishes.')
            return
        self.root.destroy()

    def safe(self, function):
        if self.busy: return
        try: function()
        except Exception as exc: messagebox.showerror('Could not complete the operation', str(exc), parent=self.root)

    def settings_path(self): return user_path(self.path.get())
    def needs_upgrade(self):
        return self.store.get('version')!=3 or any(set(values)!=self.field_keys for values in self.store['presets'].values())
    def form_values(self): return validate({k: var.get() for k, var in self.values.items()})
    def fill(self, values, priorities=None):
        self.cancel_capture()
        values = complete_bindings(values,self.schema.values()) if self.schema else validate(values)
        self.loading_bindings = True
        try:
            priorities = priorities if isinstance(priorities,dict) else {}
            self.edit_order = {key: priorities[key] if isinstance(priorities.get(key),int) and priorities[key]>0 else 0 for key in self.values}
            self.edit_counter = max(self.edit_order.values())
            for key, value in values.items(): self.values[key].set(value)
        finally: self.loading_bindings = False
        self.refresh_conflicts()
    def refresh(self):
        for child in self.preset_list.winfo_children(): child.destroy()
        self.preset_buttons.clear()
        names = CLASSES
        for name in names:
            button = tk.Button(self.preset_list, image=self.icons[name],
                               bg='#211c13', activebackground='#3b3019', relief='flat',
                               borderwidth=0, highlightthickness=2, highlightbackground='#8e7135',
                               highlightcolor='#ffd100', cursor='hand2', takefocus=True,
                               command=lambda n=name: self.safe(lambda: self.select(n)))
            button.pack(padx=5, pady=3, ipadx=3, ipady=2)
            button.bind('<MouseWheel>', lambda e: self.preset_canvas.yview_scroll(-int(e.delta / 120), 'units'))
            self.add_tooltip(button, name)
            self.preset_buttons[name] = button
        self.render_specializations()
        self.highlight_selection()

    def render_specializations(self):
        for child in self.spec_bar.winfo_children(): child.destroy()
        self.spec_buttons.clear()
        for spec, icon in SPECIALIZATIONS[self.active_class]:
            name = f'{self.active_class} - {spec}'
            button = tk.Button(self.spec_bar, image=self.spec_icons[icon],
                               bg='#211c13', activebackground='#3b3019', relief='flat',
                               borderwidth=0, highlightthickness=2, highlightbackground='#8e7135',
                               highlightcolor='#ffd100', cursor='hand2', takefocus=True,
                               command=lambda n=name: self.safe(lambda: self.select(n)))
            button.pack(side='left', padx=(0,10), ipadx=3, ipady=2)
            self.add_tooltip(button, spec)
            self.spec_buttons[name] = button

    def highlight_selection(self):
        selected_name = self.name.get()
        selected_class = selected_name.split(' - ',1)[0]
        if selected_name in SPEC_PRESETS:
            self.last_specialization[selected_class] = selected_name
        if selected_class in CLASSES and selected_class != self.active_class:
            self.active_class = selected_class
            self.render_specializations()
        for name, button in self.preset_buttons.items():
            selected = name == selected_class
            button.configure(bg='#493a17' if selected else '#211c13',
                             highlightbackground='#ffd100' if selected else '#8e7135')
        for name, button in self.spec_buttons.items():
            selected = name == selected_name
            button.configure(bg='#493a17' if selected else '#211c13',
                             highlightbackground='#ffd100' if selected else '#8e7135')
        self.selected_label.set(selected_name or 'Current bindings')

    def select(self, name):
        if name in CLASSES:
            name = self.last_specialization.get(name, f'{name} - {SPECIALIZATIONS[name][0][0]}')
        if name not in SPEC_PRESETS: return
        if not self.schema:
            self.read_current();return
        if name not in self.store['presets']:
            if not self.store['presets']:
                self.read_current()
                return
            baseline = self.store['presets'].get('Original') or read_bindings(self.settings_path().read_bytes().decode('utf-8-sig'))
            self.store['presets'][name] = dict(validate(baseline))
            self.persist()
        self.last_specialization[name.split(' - ',1)[0]] = name
        self.name.set(name)
        self.load()

    def add_tooltip(self, widget, text):
        tooltip = None
        pending = None
        def hide(event=None):
            nonlocal tooltip, pending
            if pending: self.root.after_cancel(pending); pending = None
            if tooltip: tooltip.destroy(); tooltip = None
        def show():
            nonlocal tooltip, pending
            pending = None
            tooltip = tk.Toplevel(self.root); tooltip.overrideredirect(True)
            tooltip.geometry(f'+{widget.winfo_rootx() + widget.winfo_width() + 8}+{widget.winfo_rooty() + 10}')
            tk.Label(tooltip, text=text, bg='#241f16', fg='#e9e3d3', padx=10, pady=6, font=('Georgia', 10)).pack()
        def schedule(event=None):
            nonlocal pending
            hide(); pending = self.root.after(350, show)
        widget.bind('<Enter>', schedule)
        widget.bind('<Leave>', hide)
        widget.bind('<ButtonPress-1>', hide)
        widget.bind('<Destroy>', hide)
    def persist(self):
        self.store['settings_path'] = self.path.get()
        atomic_write(self.store_path, json.dumps(self.store, indent=2, ensure_ascii=False).encode('utf-8'))
    def browse(self):
        path = filedialog.askopenfilename(parent=self.root,title='Select settings.ron', filetypes=[('RON settings', '*.ron')])
        if path: self.path.set(str(user_path(path))); self.read_current()
    def read_current(self):
        path = self.settings_path()
        if not path.exists():
            self.status.set('Open Arena of Champions and sign in first, or browse to its settings.ron file. No presets have been created.')
            return
        initialise = not self.store['presets']
        def loaded(snapshot):
            self.update_schema(snapshot['document'])
            upgrading=not initialise and self.needs_upgrade()
            self.fill(snapshot['bindings'])
            if initialise:
                self.data.mkdir(parents=True,exist_ok=True)
                try:
                    with (self.data/'original-settings.ron').open('xb') as stream: stream.write(snapshot['local'])
                except FileExistsError: pass
                if snapshot['account'] is not None and not (self.data/'original-account-settings.json').exists():
                    atomic_write(self.data/'original-account-settings.json',json.dumps({'settings':snapshot['account']},indent=2).encode())
                self.store['presets'] = {name:dict(snapshot['bindings']) for name in ['Original',*CLASSES]}
                add_specialization_presets(self.store)
                self.store['version'] = 3
                self.name.set(SPEC_PRESETS[0])
                self.persist()
                self.refresh()
            elif upgrading:
                upgraded = {name:complete_bindings(values,snapshot['bindings']) for name,values in self.store['presets'].items()}
                if self.store_path.exists():
                    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                    atomic_write(self.data/'backups'/(stamp+'-presets.json'),self.store_path.read_bytes())
                self.store['presets']=upgraded
                add_specialization_presets(self.store)
                self.store['version']=3
                self.persist()
                self.name.set(SPEC_PRESETS[0])
                self.load();self.refresh()
            else:
                self.name.set('')
                self.highlight_selection()
            self.status.set(snapshot['notice'] or ('Your presets now include all game bindings.' if upgrading else 'Current bindings saved for every specialization.' if initialise else f"{len(snapshot['bindings'])} current {snapshot['source']} bindings loaded."))
        if not any((path.parent/name).exists() for name in ('session.ron','guest.ron')):
            loaded(read_game_snapshot(path))
            return
        self.busy = True
        self.status.set('Reading your current game bindings…')
        results = queue.SimpleQueue()
        def worker():
            try: results.put((True,read_game_snapshot(path)))
            except Exception as exc: results.put((False,str(exc)))
        threading.Thread(target=worker,daemon=True).start()
        def poll():
            try: success,result = results.get_nowait()
            except queue.Empty:
                self.root.after(80,poll); return
            self.busy = False
            if success: self.safe(lambda: loaded(result))
            else:
                self.status.set('Could not read the game settings. No presets were created.')
                messagebox.showerror('Read from game',result,parent=self.root)
        self.root.after(80,poll)
    def load(self):
        self.fill(self.store['presets'][self.name.get()],self.store['binding_priorities'].get(self.name.get()))
        self.highlight_selection()
        self.status.set('')
    def save_as(self):
        values = self.form_values()
        dialog = tk.Toplevel(self.root); dialog.withdraw(); dialog.title('Save to specialization'); dialog.transient(self.root)
        dialog.configure(bg='#0d1217'); dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=22); panel.pack()
        name_var = tk.StringVar(value=self.name.get() if self.name.get() in SPEC_PRESETS else SPEC_PRESETS[0])
        ttk.Label(panel, text='Specialization').grid(row=0, column=0, sticky='w', pady=8, padx=(0, 15))
        entry = ttk.Combobox(panel, textvariable=name_var, values=SPEC_PRESETS, state='readonly', width=30)
        entry.grid(row=0, column=1)
        def save():
            name = name_var.get().strip()
            if name not in SPEC_PRESETS: return
            if name in self.store['presets'] and not messagebox.askyesno('Replace preset', f'Replace "{name}"?', parent=dialog): return
            self.store['presets'][name] = values
            self.store['binding_priorities'][name] = dict(self.edit_order)
            self.name.set(name); self.persist(); self.refresh()
            self.status.set(f'"{name}" saved.'); dialog.destroy()
        ttk.Button(panel, text='Cancel', command=dialog.destroy).grid(row=2, column=0, pady=(16, 0))
        ttk.Button(panel, text='Save', command=lambda: self.safe(save)).grid(row=2, column=1, sticky='e', pady=(16, 0))
        center_dialog(dialog,self.root)
        dialog.grab_set(); entry.focus_set()
    def update(self):
        name = self.name.get()
        if not name: return self.save_as()
        self.store['presets'][name] = self.form_values()
        self.store['binding_priorities'][name] = dict(self.edit_order)
        self.persist()
        self.status.set(f'Preset "{name}" saved.')
    def delete(self):
        name = self.name.get()
        if name in SPEC_PRESETS and messagebox.askyesno('Reset preset', f'Reset "{name}" to the original bindings?', parent=self.root):
            original = self.store['presets'].get('Original')
            if original is None:
                original = read_bindings(self.settings_path().read_bytes().decode('utf-8-sig'))
            self.store['presets'][name] = dict(validate(original))
            self.store['binding_priorities'].pop(name,None)
            self.persist(); self.load()
            self.status.set(f'"{name}" reset. Apply the preset to update the game.')
    def apply(self):
        self.start_apply(self.form_values())

    def confirm_restart(self, values, restoring, game):
        if self.restart_dialog is not None and self.restart_dialog.winfo_exists():
            self.restart_dialog.focus_force(); return
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        self.restart_dialog = dialog
        dialog.title('Restart Arena of Champions?')
        dialog.transient(self.root); dialog.resizable(False,False)
        panel = ttk.Frame(dialog,padding=24); panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Arena of Champions is running.',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Applying this preset will close Arena of Champions, update your bindings, and start the game again. Continue?',wraplength=420).pack(anchor='w',pady=(12,20))
        buttons = ttk.Frame(panel); buttons.pack(fill='x')
        def dismiss():
            self.restart_dialog = None
            dialog.destroy()
        def cancel():
            dismiss(); self.status.set('Restart cancelled.')
        def accept():
            dismiss()
            self.safe(lambda: self.start_apply(values,restoring,game,approved=True))
        cancel_button = ttk.Button(buttons,text='Cancel',command=cancel)
        cancel_button.pack(side='right')
        ttk.Button(buttons,text='Restart and apply',command=accept).pack(side='right',padx=(0,8))
        dialog.protocol('WM_DELETE_WINDOW',cancel)
        dialog.bind('<Escape>',lambda event: cancel())
        center_dialog(dialog,self.root)
        dialog.grab_set(); cancel_button.focus_set()

    def start_apply(self, values, restoring=False, game=None, approved=False):
        self.cancel_capture()
        values = validate_for_game(values)
        issues = conflicts(self.settings_path().read_bytes().decode('utf-8-sig'),values,template=self.document)
        if issues: raise ValueError('Clear or change duplicate bindings before applying:\n\n'+'\n'.join(issues))
        if not approved:
            game = running_game()
            if game is not None:
                self.confirm_restart(values,restoring,game)
                return
        path = self.settings_path()
        self.busy = True
        self.status.set('Restarting the game and applying bindings…' if game else 'Updating account and local bindings…')
        results = queue.SimpleQueue()
        def worker():
            try: results.put((True, apply_with_restart(path,values,self.data/'backups',game,apply_synced_bindings)))
            except Exception as exc: results.put((False, str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        def poll():
            try: success, result = results.get_nowait()
            except queue.Empty:
                self.root.after(80, poll); return
            self.busy = False
            if not success:
                self.status.set('Bindings were not fully applied.')
                messagebox.showerror('Could not apply preset', result, parent=self.root)
                return
            if restoring: self.fill(values)
            self.persist()
            backup,restarted = result
            self.status.set('Bindings verified. Arena of Champions was restarted.' if restarted else 'Verified: account and local bindings match. You can now start the game.')
            messagebox.showinfo('Bindings applied', 'Account and local bindings were updated and verified.\n' + ('Arena of Champions was restarted.' if restarted else 'You can now start the game.'), parent=self.root)
        self.root.after(80, poll)
    def restore(self):
        path = filedialog.askopenfilename(parent=self.root,title='Restore bindings from a backup', initialdir=self.data / 'backups', filetypes=[('RON backup', '*.ron')])
        if not path: return
        values = read_bindings(user_path(path).read_bytes().decode('utf-8-sig'))
        schema=getattr(self,'schema',None)
        baseline=schema.values() if schema else read_bindings(self.settings_path().read_bytes().decode('utf-8-sig'))
        self.start_apply(complete_bindings(values,baseline), restoring=True)
    def import_preset(self):
        path = filedialog.askopenfilename(parent=self.root,filetypes=[('Preset JSON', '*.json')])
        if not path: return
        payload = json.loads(user_path(path).read_text(encoding='utf-8-sig'))
        self.fill(complete_bindings(payload['bindings'],self.form_values()))
        self.name.set('')
        self.highlight_selection()
        self.status.set('Preset imported. Use Save to to choose its specialization.')
    def export_preset(self):
        values = self.form_values()
        path = filedialog.asksaveasfilename(parent=self.root,defaultextension='.json', initialfile=(self.name.get() or 'preset') + '.json', filetypes=[('Preset JSON', '*.json')])
        if path: atomic_write(user_path(path), json.dumps({'name': self.name.get(), 'bindings': values}, indent=2, ensure_ascii=False).encode('utf-8'))

    def sync_display(self, key):
        if not self.capture_state or self.capture_state['key'] != key:
            self.displays[key].set(self.values[key].get())
        if not self.loading_bindings:
            self.edit_counter += 1
            self.edit_order[key] = self.edit_counter
            self.refresh_conflicts()

    def refresh_conflicts(self):
        groups = {}
        for key,value in self.values.items():
            binding = value.get()
            if binding: groups.setdefault(binding,[]).append(key)
        red = set()
        for keys in groups.values():
            if len(keys)<2: continue
            newest = max(self.edit_order[key] for key in keys)
            if not newest: red.update(keys)
            else: red.update(key for key in keys if self.edit_order[key]<newest)
        self.conflict_fields = red
        for key,entry in self.binding_entries.items():
            listening = self.capture_state and self.capture_state['key']==key
            background = '#493a17' if listening else '#3b181c' if key in red else '#211c13'
            entry.configure(style='Listening.TEntry' if listening else 'Conflict.TEntry' if key in red else 'Binding.TEntry')
            self.binding_labels[key].configure(style='Conflict.TLabel' if key in red else 'TLabel')
            self.binding_frames[key].configure(bg=background,highlightbackground='#ffd100' if listening else '#d95160' if key in red else '#8e7135')
            self.clear_buttons[key].configure(bg=background)

    def clear_binding(self,key):
        if self.busy: return 'break'
        self.cancel_capture()
        self.values[key].set('')
        self.status.set('')
        return 'break'

    def capture_focus_out(self, event):
        if self.capture_state:
            self.root.after_idle(self.cancel_if_unfocused)

    def cancel_if_unfocused(self):
        if self.capture_state and self.root.focus_get() is None:
            self.cancel_capture()

    def cancel_capture(self):
        state = self.capture_state
        if not state: return
        self.capture_state = None
        if state.get('timer'):
            self.root.after_cancel(state['timer'])
        for widget, tags in state['widgets']:
            if widget.winfo_exists(): widget.bindtags(tags)
        for sequence in ('<KeyPress>', '<ButtonPress>', '<MouseWheel>'):
            self.root.unbind_class(state['tag'], sequence)
        key = state['key']
        self.displays[key].set(self.values[key].get())
        if self.binding_entries[key].winfo_exists():
            self.refresh_conflicts()

    def finish_capture(self, base, event=None):
        if not self.capture_state: return 'break'
        modifiers = []
        event_state = getattr(event, 'state', 0)
        for vk, mask, name in [(0x11, 0x4, 'Ctrl'), (0x12, 0x20000, 'Alt'), (0x10, 0x1, 'Shift')]:
            down = os.name == 'nt' and ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000
            if down or event_state & mask: modifiers.append(name)
        try: value = canonical('-'.join(modifiers + [base]))
        except ValueError: return 'break'
        key = self.capture_state['key']
        self.cancel_capture()
        self.values[key].set(value)
        self.status.set('')
        return 'break'

    def capture_keypress(self, event):
        vk = event.keycode
        if vk == 27:
            self.cancel_capture()
            return 'break'
        if vk in (16, 17, 18, 160, 161, 162, 163, 164, 165): return 'break'
        keys = {8:'Backspace',9:'Tab',13:'Enter',20:'CapsLock',32:'Space',33:'PageUp',34:'PageDown',35:'End',36:'Home',37:'ArrowLeft',38:'ArrowUp',39:'ArrowRight',40:'ArrowDown',45:'Insert',46:'Delete',106:'NumpadMultiply',107:'NumpadAdd',109:'NumpadSubtract',110:'NumpadDecimal',111:'NumpadDivide',186:'Semicolon',187:'Equal',188:'Comma',189:'Minus',190:'Period',191:'Slash',192:'Backquote',219:'BracketLeft',220:'Backslash',221:'BracketRight',222:'Quote'}
        if 65 <= vk <= 90: base = 'Key' + chr(vk)
        elif 48 <= vk <= 57: base = 'Digit' + chr(vk)
        elif 112 <= vk <= 135: base = 'F' + str(vk-111)
        elif 96 <= vk <= 105: base = 'Numpad' + str(vk-96)
        else: base = keys.get(vk)
        if base: self.finish_capture(base, event)
        return 'break'

    def capture(self, key):
        if self.busy: return 'break'
        self.cancel_capture()
        tag = 'AoCInlineCapture' + str(id(self))
        state = {'key': key, 'tag': tag, 'widgets': [], 'timer': None}
        self.capture_state = state
        self.displays[key].set('Press a key or button')
        self.binding_entries[key].configure(style='Listening.TEntry')
        self.refresh_conflicts()
        self.binding_entries[key].focus_set()
        # A temporary first bindtag consumes the next input before any button,
        # entry, scrollbar or shortcut handles it. The opening click has already
        # reached its widget binding, so it cannot bind itself as MouseLeft.
        def attach(widget):
            tags = widget.bindtags()
            state['widgets'].append((widget, tags))
            widget.bindtags((tag,) + tags)
            for child in widget.winfo_children(): attach(child)
        attach(self.root)
        self.root.bind_class(tag, '<KeyPress>', self.capture_keypress)
        def mouse(event):
            for target,button in self.clear_buttons.items():
                if event.widget==button: return self.clear_binding(target)
            base = {1:'MouseLeft',2:'MouseMiddle',3:'MouseRight',4:'Mouse4',5:'Mouse5'}.get(event.num)
            if base: self.finish_capture(base, event)
            return 'break'
        self.root.bind_class(tag, '<ButtonPress>', mouse)
        self.root.bind_class(tag, '<MouseWheel>', lambda event: self.finish_capture('MouseWheelUp' if event.delta > 0 else 'MouseWheelDown', event))
        # Tk on Windows does not reliably deliver XBUTTON events. Poll the side
        # buttons only while this app owns focus and a field is listening.
        if os.name == 'nt':
            previous = {vk: bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000) for vk in (5, 6)}
            def side_buttons():
                if self.capture_state is not state: return
                if self.root.focus_get() is None:
                    self.cancel_capture(); return
                for vk, base in [(5, 'Mouse4'), (6, 'Mouse5')]:
                    down = bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
                    if down and not previous[vk]: self.finish_capture(base); return
                    previous[vk] = down
                state['timer'] = self.root.after(20, side_buttons)
            state['timer'] = self.root.after(20, side_buttons)
        return 'break'


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--install-update':
        if not getattr(sys,'frozen',False):sys.exit(2)
        try:
            from app_updates import install_update
            sys.exit(0 if install_update(sys.argv[2]) else 1)
        except Exception:sys.exit(1)
    try:
        if os.name == 'nt':
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('AoC.BindPresets')
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass
    smoke = len(sys.argv) == 3 and sys.argv[1] == '--smoke-test'
    scratch = tempfile.TemporaryDirectory() if smoke else None
    if smoke: BASE = Path(scratch.name)
    root = tk.Tk()
    try: app = App(root)
    except Exception as exc:
        messagebox.showerror('Startup error', str(exc), parent=root); root.destroy(); sys.exit(1)
    if smoke:
        def finish_smoke():
            if app.busy:
                root.after(100,finish_smoke)
                return
            result = {'status': 'ok', 'bindings': app.form_values(), 'presets': list(app.store['presets']),
                      'initialised': all(name in app.store['presets'] for name in CLASSES),
                      'binding_count': len(app.binding_entries),
                      'title': root.title(), 'class_buttons': len(app.preset_buttons),
                      'window': [root.winfo_width(), root.winfo_height()]}
            atomic_write(Path(sys.argv[2]), json.dumps(result, indent=2).encode())
            root.destroy()
        root.after(500, finish_smoke)
    else:
        from app_updates import UpdateController
        updated=sys.argv[2] if len(sys.argv)==3 and sys.argv[1]=='--updated' else None
        app.updater=UpdateController(app)
        app.updater.start(check=bool(getattr(sys,'frozen',False)) and not updated and '--skip-update-check' not in sys.argv,cleanup=updated)
    root.mainloop()
    if scratch: scratch.cleanup()
