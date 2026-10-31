from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import presets
from binding_schema import Schema

FIXTURE=Path(__file__).parent/'fixtures'/'settings.ron'


def extended(text):
    return text.replace('keys: (','keys: (\n        interrupt_focus: "Ctrl-KeyB",\n        quick_keys: ["F13", "F14"],\n        context: (cast_here: "Ctrl-KeyZ",),\n        revision: 2,',1).replace('"CapsLock"),','"CapsLock", "F15", "F16"),',1)


class SchemaTests(unittest.TestCase):
    def setUp(self):self.text=FIXTURE.read_text()

    def test_new_fields_lists_and_nested_bindings_are_discovered(self):
        values=presets.read_bindings(extended(self.text))
        self.assertEqual(len(values),35)
        self.assertEqual(values['interrupt_focus'],'Ctrl-KeyB')
        self.assertEqual(values['quick_keys[1]'],'F14')
        self.assertEqual(values['context.cast_here'],'Ctrl-KeyZ')
        self.assertNotIn('revision',values)
        columns=presets.binding_columns(list(values))
        groups={name:fields for column in columns for name,fields in column}
        self.assertEqual(len(groups['Action Bar']),10)
        self.assertIn(('interrupt_focus','Interrupt focus'),groups['Other'])

    def test_patches_preserve_comments_metadata_and_other_settings(self):
        text=extended(self.text).replace('revision: 2,','/* keys: (fake: "KeyX") */\n        revision: 2,')
        values=presets.read_bindings(text)
        original=dict(values);values['interrupt_focus']='Ctrl-KeyC'
        changed=presets.patch_settings(text,values)
        self.assertEqual(changed,text.replace('interrupt_focus: "Ctrl-KeyB"','interrupt_focus: "Ctrl-KeyC"'))
        self.assertEqual(presets.patch_settings(changed,original),text)

    def test_can_extend_old_account_settings_without_losing_account_data(self):
        source=extended(self.text)
        target=self.text.replace('keys: (','keys: (\n        account_only: "F20",\n        revision: 1,',1).replace('camera_speed: 1.0','camera_speed: 2.0')
        values=presets.read_bindings(source);values['interrupt_focus']='Ctrl-KeyC'
        changed=presets.patch_settings(target,values,template=source)
        actual=presets.read_bindings(changed)
        self.assertTrue(all(actual[key]==value for key,value in values.items()))
        self.assertEqual(actual['account_only'],'F20')
        self.assertIn('camera_speed: 2.0',changed)
        self.assertIn('revision: 1',changed)

    def test_unknown_bindings_participate_in_conflicts(self):
        text=extended(self.text);values=presets.read_bindings(text)
        values['interrupt_focus']='KeyF'
        self.assertTrue(any('Interrupt focus' in issue and 'Action 3' in issue for issue in presets.conflicts(text,values)))

    def test_application_updates_all_new_bindings_on_account_and_local_file(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);path=folder/'settings.ron';path.write_text(extended(self.text))
            (folder/'session.ron').write_text('(token: "fake")')
            state={'settings':self.text};methods=[]
            def api(method,payload,token):
                methods.append(method)
                if method=='PUT':state.update(payload);return {}
                return dict(state)
            values=presets.read_bindings(path.read_text());values['action_10']='Shift-F16'
            presets.apply_synced_bindings(path,values,folder/'backups',lambda:False,api)
            self.assertEqual(methods,['GET','PUT','GET'])
            self.assertEqual(presets.read_bindings(state['settings']),values)
            self.assertEqual(presets.read_bindings(path.read_text()),values)

    def test_invalid_structures_fail_before_a_patch_is_written(self):
        for text in ['(keys: (foo: "KeyQ", foo: "KeyE"))','(keys: (foo: "KeyQ")','(keys: (foo: "KeyQ" bar: "KeyE"))']:
            with self.subTest(text=text),self.assertRaises(ValueError):Schema(text)

    def test_removed_bindings_do_not_remain_in_the_current_profile(self):
        values=presets.read_bindings(extended(self.text));values['action_1']='Ctrl-KeyQ'
        completed=presets.complete_bindings(values,presets.read_bindings(self.text))
        self.assertEqual(len(completed),29)
        self.assertEqual(completed['action_1'],'Ctrl-KeyQ')
        self.assertNotIn('interrupt_focus',completed)

    def test_interface_and_saved_presets_follow_a_changed_file(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);path=folder/'game'/'settings.ron';path.parent.mkdir();path.write_text(self.text)
            with patch.object(presets,'BASE',folder/'app'),patch.object(presets,'DEFAULT_SETTINGS',path):
                root=presets.tk.Tk()
                try:
                    app=presets.App(root);root.update()
                    app.select('Mage');app.values['action_1'].set('Ctrl-KeyQ');app.update()
                    saved=app.store_path.read_bytes()
                    path.write_text(extended(self.text))
                    app.read_current();root.update()
                    self.assertEqual(len(app.binding_entries),35)
                    self.assertIn('interrupt_focus',app.binding_entries)
                    self.assertEqual(app.store['presets']['Mage']['action_1'],'Ctrl-KeyQ')
                    self.assertEqual(app.store['presets']['Mage']['action_10'],'F16')
                    backups=[p.read_bytes() for p in (app.data/'backups').glob('*-presets.json')]
                    self.assertIn(saved,backups)
                    app.values['interrupt_focus'].set('KeyF')
                    self.assertIn('action_3',app.conflict_fields)
                    path.write_text(self.text)
                    app.read_current();root.update()
                    self.assertEqual(len(app.binding_entries),29)
                    self.assertNotIn('interrupt_focus',app.values)
                    self.assertEqual(app.store['presets']['Mage']['action_1'],'Ctrl-KeyQ')
                finally:root.destroy()

    def test_old_account_does_not_hide_new_bindings_from_the_game_file(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);path=folder/'settings.ron';path.write_text(extended(self.text))
            (folder/'session.ron').write_text('(token: "fake")')
            snapshot=presets.read_game_snapshot(path,lambda *args:{'settings':self.text})
            self.assertEqual(len(snapshot['bindings']),35)
            self.assertEqual(snapshot['bindings']['action_10'],'F16')


if __name__=='__main__':unittest.main(verbosity=2)
