import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import presets


FIXTURE = Path(__file__).parent/'fixtures'/'settings.ron'


class SpecializationTests(unittest.TestCase):
    def test_migration_copies_each_class_and_priorities_without_overwriting_specializations(self):
        store = {'presets': {'Original': {'action_1': 'Digit1'},
                             'Mage': {'action_1': 'KeyQ'},
                             'Mage - Fire': {'action_1': 'KeyE'}},
                 'binding_priorities': {'Mage': {'action_1': 8}}}
        self.assertTrue(presets.add_specialization_presets(store))
        self.assertEqual(store['presets']['Mage - Arcane'], {'action_1': 'KeyQ'})
        self.assertEqual(store['presets']['Mage - Fire'], {'action_1': 'KeyE'})
        self.assertEqual(store['presets']['Druid - Balance'], {'action_1': 'Digit1'})
        self.assertEqual(store['binding_priorities']['Mage - Arcane'], {'action_1': 8})
        store['presets']['Mage - Arcane']['action_1'] = 'KeyR'
        store['binding_priorities']['Mage - Arcane']['action_1'] = 9
        self.assertEqual(store['presets']['Mage - Frost']['action_1'], 'KeyQ')
        self.assertEqual(store['binding_priorities']['Mage']['action_1'], 8)
        self.assertFalse(presets.add_specialization_presets(store))

    def test_existing_class_presets_are_backed_up_and_specializations_save_independently(self):
        values=presets.read_bindings(FIXTURE.read_text(encoding='utf-8-sig'))
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp); data=folder/'data'; data.mkdir()
            mage=dict(values); mage['action_1']='Ctrl-KeyQ'
            previous={'version':3,'settings_path':str(FIXTURE),
                      'presets':{'Original':values,'Warrior':dict(values),'Mage':mage},
                      'binding_priorities':{'Mage':{'action_1':10}}}
            raw=json.dumps(previous).encode();(data/'presets.json').write_bytes(raw)
            with patch.object(presets,'BASE',folder):
                root=tk.Tk()
                try:
                    app=presets.App(root);root.update()
                    self.assertEqual(next((data/'backups').glob('*-presets.json')).read_bytes(),raw)
                    for spec,_ in presets.SPECIALIZATIONS['Mage']:
                        self.assertEqual(app.store['presets']['Mage - '+spec],mage)
                    app.preset_buttons['Mage'].invoke()
                    self.assertEqual(len(app.spec_buttons),3)
                    app.spec_buttons['Mage - Fire'].invoke()
                    self.assertEqual(app.edit_order['action_1'],10)
                    app.values['action_1'].set('Alt-KeyZ');app.update()
                    app.select('Druid')
                    self.assertEqual(list(app.spec_buttons),['Druid - Balance','Druid - Restoration','Druid - Feral'])
                    app.select('Mage')
                    self.assertEqual(app.name.get(),'Mage - Fire')
                    self.assertEqual(app.form_values()['action_1'],'Alt-KeyZ')
                    app.select('Mage - Frost')
                    self.assertEqual(app.form_values()['action_1'],'Ctrl-KeyQ')
                    self.assertEqual(app.store['presets']['Mage'],mage)
                finally:root.destroy()
                root=tk.Tk()
                try:
                    app=presets.App(root);app.select('Mage - Fire')
                    self.assertEqual(app.form_values()['action_1'],'Alt-KeyZ')
                    app.start_apply=Mock();app.apply()
                    self.assertEqual(app.start_apply.call_args.args[0]['action_1'],'Alt-KeyZ')
                    with patch.object(presets.messagebox,'askyesno',return_value=True):app.delete()
                    self.assertEqual(app.form_values(),values)
                    self.assertEqual(app.store['presets']['Mage - Frost'],mage)
                finally:root.destroy()

    def test_save_to_targets_a_specialization(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(presets,'BASE',Path(temp)),patch.object(presets,'DEFAULT_SETTINGS',FIXTURE):
            root=tk.Tk()
            try:
                app=presets.App(root);app.values['action_1'].set('Ctrl-KeyQ')
                app.save_as();root.update()
                dialog=next(child for child in root.winfo_children() if isinstance(child,tk.Toplevel))
                panel=dialog.winfo_children()[0]
                choice=next(child for child in panel.winfo_children() if isinstance(child,presets.ttk.Combobox))
                self.assertEqual(len(choice['values']),27)
                choice.set('Druid - Restoration')
                save=next(child for child in panel.winfo_children() if isinstance(child,presets.ttk.Button) and child.cget('text')=='Save')
                with patch.object(presets.messagebox,'askyesno',return_value=True):save.invoke()
                self.assertEqual(app.name.get(),'Druid - Restoration')
                self.assertEqual(app.store['presets']['Druid - Restoration']['action_1'],'Ctrl-KeyQ')
                self.assertNotEqual(app.store['presets']['Druid - Feral']['action_1'],'Ctrl-KeyQ')
                self.assertEqual(app.active_class,'Druid')
            finally:root.destroy()
