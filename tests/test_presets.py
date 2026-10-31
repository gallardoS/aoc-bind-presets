from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
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
            values = self.modified(); values['action_3']=''; values['class_3']=''
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

    def test_remote_conflict_preserves_both_documents(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); path = folder / 'settings.ron'; path.write_bytes(self.original)
            (folder / 'session.ron').write_text('(token: "fake")')
            remote = self.text.replace('forward: "KeyW"', 'forward: "Ctrl-KeyQ"')
            def api(method, payload, token):
                self.assertEqual(method, 'GET')
                return {'settings': remote}
            with self.assertRaises(ValueError):
                presets.apply_synced_bindings(path, self.modified(), folder / 'backups', lambda: False, api)
            self.assertEqual(path.read_bytes(), self.original)

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
