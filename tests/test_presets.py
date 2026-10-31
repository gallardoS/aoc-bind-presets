from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import time
import presets


FIXTURE = Path(__file__).parent / 'fixtures' / 'settings.ron'


@patch.object(presets, 'DEFAULT_SETTINGS', FIXTURE)
class PresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = FIXTURE.read_bytes()
        cls.text = cls.original.decode('utf-8-sig')

    def modified(self):
        values = presets.read_bindings(self.text)
        values['action_1'] = 'Ctrl-KeyQ'
        values['utility_2'] = 'Alt-KeyZ'
        return values

    def test_only_target_values_change_and_roundtrip_is_exact(self):
        initial = presets.read_bindings(self.text)
        changed = presets.patch_settings(self.text, self.modified())
        self.assertEqual(presets.patch_settings(changed, initial), self.text)
        expected = self.text.replace('hotbar: ("' + initial['action_1'] + '"', 'hotbar: ("Ctrl-KeyQ"', 1)
        expected = expected.replace('utility_2: "' + initial['utility_2'] + '"', 'utility_2: "Alt-KeyZ"', 1)
        self.assertEqual(changed, expected)

    def test_conflict_with_movement_is_detected(self):
        values = self.modified(); values['action_1'] = 'KeyW'
        self.assertTrue(any('forward' in issue for issue in presets.conflicts(self.text, values)))

    def test_conflict_inside_bar_is_detected(self):
        values = self.modified(); values['action_2'] = values['action_1']
        self.assertTrue(presets.conflicts(self.text, values))

    def test_rejects_malformed_or_injected_values(self):
        for value in ['Ctrl-Ctrl-KeyQ', 'KeyQ", forward: "KeyZ', 'Shift-Foo']:
            with self.assertRaises(ValueError): presets.canonical(value)

    def test_restore_accepts_pasted_paths_with_quotes(self):
        app=presets.App.__new__(presets.App)
        app.root=Mock()
        app.data=FIXTURE.parent
        app.path=Mock();app.path.get.return_value=str(FIXTURE)
        app.start_apply=Mock()
        for path in ['"'+str(FIXTURE)+'"','"'+str(FIXTURE), '\\"'+str(FIXTURE)+'\\"',str(FIXTURE)]:
            with patch.object(presets.filedialog,'askopenfilename',return_value=path): app.restore()
            app.start_apply.assert_called_with(presets.read_bindings(self.text),restoring=True)

    def test_empty_bindings_roundtrip_and_do_not_conflict(self):
        values = self.modified(); values['action_1']=''; values['action_2']=''; values['class_2']=''
        changed = presets.patch_settings(self.text,values)
        self.assertEqual(presets.read_bindings(changed),values)
        self.assertEqual(presets.conflicts(self.text,values),[])
        self.assertEqual(presets.canonical(''),'')

    def test_unrecognized_schema_fails_closed(self):
        with self.assertRaises(ValueError): presets.patch_settings(self.text.replace('hotbar:', 'new_hotbar:'), self.modified())

    def test_apply_backup_and_restore_preserving_new_unrelated_setting(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); path = folder / 'settings.ron'
            path.write_bytes(self.original)
            backup = presets.apply_bindings(path, self.modified(), folder / 'backups', lambda: False)
            self.assertEqual(backup.read_bytes(), self.original)
            current = path.read_bytes().replace(b'camera_speed: 1.0', b'camera_speed: 2.0')
            path.write_bytes(current)
            presets.apply_bindings(path, presets.read_bindings(backup.read_bytes().decode('utf-8-sig')), folder / 'backups', lambda: False)
            self.assertEqual(path.read_bytes(), self.original.replace(b'camera_speed: 1.0', b'camera_speed: 2.0'))

    def test_open_game_blocks_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); path = folder / 'settings.ron'; path.write_bytes(self.original)
            with self.assertRaises(ValueError): presets.apply_bindings(path, self.modified(), folder / 'backups', lambda: True)
            self.assertEqual(path.read_bytes(), self.original)
            self.assertFalse((folder / 'backups').exists())

    def test_preserves_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); path = folder / 'settings.ron'
            source = b'\xef\xbb\xbf' + self.text.replace('\r\n', '\n').replace('\n', '\r\n').encode()
            path.write_bytes(source)
            presets.apply_bindings(path, self.modified(), folder / 'backups', lambda: False)
            self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertEqual(source.count(b'\r\n'), path.read_bytes().count(b'\r\n'))

    def test_account_apply_changes_only_bindings_and_verifies_remote(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); path = folder / 'settings.ron'; path.write_bytes(self.original)
            (folder / 'session.ron').write_text('(token: "test-session-secret")')
            remote = '(settings:' + self.text.strip() + ',other:"keep")'
            state = {'settings': remote}; calls = []
            def api(method, payload, token):
                self.assertEqual(token, 'test-session-secret'); calls.append(method)
                if method == 'PUT': state.update(payload); return {}
                return dict(state)
            values = self.modified()
            presets.apply_synced_bindings(path, values, folder / 'backups', lambda: False, api)
            self.assertEqual(calls, ['GET', 'PUT', 'GET'])
            self.assertEqual(state['settings'], presets.patch_settings(remote, values))
            self.assertEqual(presets.read_bindings(path.read_bytes().decode('utf-8-sig')), values)
            self.assertEqual(presets.patch_settings(state['settings'], presets.read_bindings(self.text)), remote)
            for backup in (folder / 'backups').iterdir():
                self.assertNotIn(b'test-session-secret', backup.read_bytes())

    def test_failed_account_update_never_reports_local_success(self):
        for reject_put in (True, False):
            with self.subTest(reject_put=reject_put), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary); path = folder / 'settings.ron'; path.write_bytes(self.original)
                (folder / 'session.ron').write_text('(token: "fake")')
                def api(method, payload, token):
                    if method == 'PUT' and reject_put: raise ValueError('Rejected')
                    return {'settings': self.text}
                with self.assertRaises(ValueError):
                    presets.apply_synced_bindings(path, self.modified(), folder / 'backups', lambda: False, api)
                self.assertEqual(path.read_bytes(), self.original)

    def test_empty_binding_is_never_sent_to_the_game_or_account(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); path=folder/'settings.ron'; path.write_bytes(self.original)
            values=self.modified(); values['action_3']=''
            api=Mock()
            with self.assertRaisesRegex(ValueError,'cannot load empty bindings'):
                presets.apply_synced_bindings(path,values,folder/'backups',lambda:False,api)
            api.assert_not_called()
            self.assertEqual(path.read_bytes(),self.original)

    def test_first_launch_imports_account_bindings_for_every_class_and_keeps_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary); game=folder/'game'; game.mkdir()
            path=game/'settings.ron'; path.write_bytes(self.original)
            (game/'session.ron').write_text('(token: "fake-session")')
            custom=self.modified(); remote='(settings:'+presets.patch_settings(self.text,custom).strip()+')'
            api=Mock(return_value={'settings':remote})
            with patch.object(presets,'BASE',folder/'app'),patch.object(presets,'DEFAULT_SETTINGS',path),patch.object(presets,'account_api',api):
                root=presets.tk.Tk()
                try:
                    app=presets.App(root)
                    deadline=time.monotonic()+3
                    while app.busy and time.monotonic()<deadline:
                        root.update(); time.sleep(.01)
                    self.assertFalse(app.busy)
                    for name in ['Original',*presets.CLASSES]: self.assertEqual(app.store['presets'][name],custom)
                    self.assertEqual((app.data/'original-settings.ron').read_bytes(),self.original)
                    self.assertNotIn('fake-session',(app.data/'original-account-settings.json').read_text())
                    api.assert_called_once_with('GET',None,'fake-session')
                    app.select('Mage'); app.values['action_1'].set('Alt-KeyB'); app.update()
                finally: root.destroy()
                api.reset_mock()
                path.write_bytes(self.original)
                root=presets.tk.Tk()
                try:
                    app=presets.App(root);root.update()
                    self.assertEqual(app.store['presets']['Mage']['action_1'],'Alt-KeyB')
                    self.assertEqual(app.store['presets']['Warrior'],custom)
                    self.assertEqual((app.data/'original-settings.ron').read_bytes(),self.original)
                    api.assert_not_called()
                finally: root.destroy()

    def test_missing_settings_waits_until_a_real_file_is_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary); path=folder/'game'/'settings.ron'
            with patch.object(presets,'BASE',folder/'app'),patch.object(presets,'DEFAULT_SETTINGS',path):
                root=presets.tk.Tk()
                try:
                    app=presets.App(root);root.update();app.select('Mage')
                    self.assertEqual(app.store['presets'],{})
                    self.assertFalse(app.store_path.exists())
                    path.parent.mkdir();path.write_bytes(presets.patch_settings(self.text,self.modified()).encode())
                    app.read_current()
                    for name in presets.CLASSES: self.assertEqual(app.store['presets'][name],self.modified())
                finally:root.destroy()

    def test_reading_offline_preserves_custom_local_bindings(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);path=folder/'settings.ron'
            path.write_bytes(presets.patch_settings(self.text,self.modified()).encode())
            (folder/'session.ron').write_text('(token: "fake")')
            snapshot=presets.read_game_snapshot(path,Mock(side_effect=ValueError('Offline')))
            self.assertEqual(snapshot['bindings'],self.modified())
            self.assertEqual(snapshot['source'],'game')
            self.assertTrue(snapshot['notice'])

    def test_full_restore_resolves_the_remote_focus_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);path=folder/'settings.ron'
            changed=self.text.replace('focus: "Digit5"','focus: "KeyF"')
            path.write_bytes(changed.encode());(folder/'session.ron').write_text('(token: "fake")')
            state={'settings':changed.replace('camera_speed: 1.0','camera_speed: 2.0')}
            def api(method,payload,token):
                if method=='PUT': state.update(payload);return {}
                return dict(state)
            original=presets.read_bindings(self.text)
            presets.apply_synced_bindings(path,original,folder/'backups',lambda:False,api)
            self.assertEqual(presets.read_bindings(state['settings'])['focus'],'Digit5')
            self.assertEqual(presets.read_bindings(state['settings'])['action_3'],'KeyF')
            self.assertIn('camera_speed: 2.0',state['settings'])
            self.assertEqual(presets.read_bindings(path.read_text()),original)

    def test_all_game_binding_groups_are_present(self):
        values=presets.read_bindings(self.text)
        self.assertEqual(len(values),29)
        self.assertEqual(set(values),presets.FIELD_KEYS)
        self.assertEqual(len({key for groups in presets.BINDING_COLUMNS for _,fields in groups for key,_ in fields}),29)

    def test_old_presets_are_upgraded_without_losing_combat_bindings(self):
        import json
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);data=folder/'data';data.mkdir()
            full=self.modified()
            combat={key:value for key,value in full.items() if key in presets.COMBAT_KEYS}
            previous={'version':1,'settings_path':str(FIXTURE),'presets':{'Original':dict(combat),'Warrior':dict(combat),'Mage':dict(combat)}}
            previous['presets']['Mage']['action_2']='Alt-KeyB'
            raw=json.dumps(previous,indent=2).encode();(data/'presets.json').write_bytes(raw)
            with patch.object(presets,'BASE',folder):
                root=presets.tk.Tk()
                try:
                    app=presets.App(root);root.update()
                    self.assertEqual(app.store['version'],3)
                    self.assertEqual(len(app.binding_entries),29)
                    self.assertEqual({key:app.store['presets']['Mage'][key] for key in combat},previous['presets']['Mage'])
                    self.assertEqual(app.store['presets']['Mage']['focus'],'Digit5')
                    self.assertEqual(app.store['presets']['Mage']['forward'],'KeyW')
                    self.assertEqual(next((data/'backups').glob('*-presets.json')).read_bytes(),raw)
                    self.assertEqual(presets.DEFAULT_SETTINGS.read_bytes(),self.original)
                finally:root.destroy()

    def test_importing_combat_only_json_keeps_the_selected_controls(self):
        baseline=presets.read_bindings(self.text);baseline['focus']='Ctrl-KeyB'
        combat={key:value for key,value in self.modified().items() if key in presets.COMBAT_KEYS}
        completed=presets.complete_bindings(combat,baseline)
        self.assertEqual(completed['action_1'],'Ctrl-KeyQ')
        self.assertEqual(completed['focus'],'Ctrl-KeyB')
        self.assertEqual(len(completed),29)

    def test_gui_startup_does_not_modify_game_settings(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(presets, 'BASE', Path(temporary)):
            root = presets.tk.Tk()
            try:
                app = presets.App(root); root.update()
                self.assertEqual(presets.DEFAULT_SETTINGS.read_bytes(), self.original)
                self.assertEqual(set(app.store['presets']), {'Original', *presets.CLASSES})
                self.assertEqual(root.title(), 'AoC-bind-presets')
                self.assertEqual(list(app.preset_buttons), ['Warrior', 'Mage', 'Priest', 'Rogue', 'Hunter', 'Paladin', 'Warlock', 'Druid', 'Shaman'])
                self.assertEqual(app.form_values(), presets.read_bindings(self.text))
                self.assertEqual(set(app.binding_entries),presets.FIELD_KEYS)
                app.store['presets']['Mage']['action_1'] = 'Ctrl-KeyQ'
                app.preset_buttons['Mage'].invoke()
                self.assertEqual(app.name.get(), 'Mage')
                self.assertEqual(app.values['action_1'].get(), 'Ctrl-KeyQ')
                self.assertEqual(presets.DEFAULT_SETTINGS.read_bytes(), self.original)
                clipped = []
                def inspect(widget):
                    for child in widget.winfo_children():
                        if child.winfo_ismapped() and not isinstance(widget, presets.tk.Canvas) and (child.winfo_x() + child.winfo_width() > widget.winfo_width() + 2 or child.winfo_y() + child.winfo_height() > widget.winfo_height() + 2):
                            clipped.append(str(child))
                        inspect(child)
                inspect(root)
                self.assertFalse(clipped, clipped)
                root.focus_force(); root.update()
                before = app.values['action_1'].get()
                windows = [w for w in root.winfo_children() if isinstance(w, presets.tk.Toplevel)]
                app.binding_labels['action_1'].event_generate('<ButtonPress-1>')
                self.assertEqual(app.displays['action_1'].get(), 'Press a key or button')
                self.assertEqual(app.values['action_1'].get(), before)
                self.assertEqual([w for w in root.winfo_children() if isinstance(w, presets.tk.Toplevel)], windows)
                app.binding_entries['action_1'].event_generate('<KeyPress>', keycode=82, state=4)
                self.assertEqual(app.values['action_1'].get(), 'Ctrl-KeyR')
                self.assertIsNone(app.capture_state)
                app.binding_entries['action_2'].event_generate('<ButtonPress-1>')
                self.assertEqual(app.displays['action_2'].get(), 'Press a key or button')
                app.binding_entries['action_2'].event_generate('<ButtonPress-3>')
                self.assertEqual(app.values['action_2'].get(), 'MouseRight')
                app.capture('action_3')
                app.binding_entries['action_3'].event_generate('<KeyPress>', keycode=27)
                self.assertIsNone(app.capture_state)
                self.assertEqual(app.displays['action_3'].get(), app.values['action_3'].get())
                app.values['focus'].set('Ctrl-KeyB')
                app.values['action_3'].set('Ctrl-KeyB')
                self.assertIn('focus',app.conflict_fields)
                app.values['focus'].set('Digit5')
                self.assertNotIn('focus',app.conflict_fields)
                app.values['action_1'].set('Ctrl-KeyQ')
                app.values['action_2'].set('Ctrl-KeyR')
                app.capture('action_2')
                app.finish_capture('KeyQ',type('Event',(),{'state':4})())
                self.assertIn('action_1',app.conflict_fields)
                self.assertNotIn('action_2',app.conflict_fields)
                self.assertEqual(app.binding_entries['action_1'].cget('style'),'Conflict.TEntry')
                app.update(); app.load()
                self.assertIn('action_1',app.conflict_fields)
                self.assertNotIn('action_2',app.conflict_fields)
                app.clear_buttons['action_1'].event_generate('<ButtonPress-1>')
                self.assertEqual(app.values['action_1'].get(),'')
                self.assertEqual(app.displays['action_1'].get(),'')
                self.assertNotIn('action_1',app.conflict_fields)
                self.assertNotIn('action_2',app.conflict_fields)
                previous = app.values['action_3'].get()
                app.capture('action_3')
                app.clear_buttons['action_2'].event_generate('<ButtonPress-1>')
                self.assertIsNone(app.capture_state)
                self.assertEqual(app.values['action_2'].get(),'')
                self.assertEqual(app.values['action_3'].get(),previous)
                app.update()
                saved = __import__('json').loads(app.store_path.read_text())
                self.assertEqual(saved['presets']['Mage']['action_1'],'')
                self.assertEqual(presets.DEFAULT_SETTINGS.read_bytes(), self.original)
            finally: root.destroy()


if __name__ == '__main__': unittest.main(verbosity=2)
