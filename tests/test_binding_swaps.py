from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import presets


FIXTURE=Path(__file__).parent/'fixtures'/'settings.ron'


class BindingSwapTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.base=patch.object(presets,'BASE',Path(self.folder.name));self.base.start()
        self.settings=patch.object(presets,'DEFAULT_SETTINGS',FIXTURE);self.settings.start()
        self.root=tk.Tk();self.app=presets.App(self.root);self.root.update()

    def tearDown(self):
        self.root.destroy();self.settings.stop();self.base.stop();self.folder.cleanup()

    def test_inline_capture_swaps_both_actions_and_only_highlights_the_previous_owner(self):
        app=self.app;before=app.form_values();old=before['action_1'];taken=before['action_2']
        # Capture the fixture's key with its modifiers without depending on
        # physical keys held by someone using the computer during this test.
        parts=taken.split('-');state=sum({'Ctrl':4,'Alt':0x20000,'Shift':1}[part] for part in parts[:-1])
        with patch.object(presets.os,'name','posix'):
            app.capture('action_1');app.finish_capture(parts[-1],type('Event',(),{'state':state})())
        self.assertEqual(app.values['action_1'].get(),taken)
        self.assertEqual(app.values['action_2'].get(),old)
        self.assertEqual(app.displays['action_2'].get(),old)
        self.assertEqual(app.binding_entries['action_2'].cget('style'),'Swapped.TEntry')
        self.assertEqual(app.binding_entries['action_1'].cget('style'),'Binding.TEntry')
        self.assertFalse(app.conflict_fields)
        self.assertEqual({k:v for k,v in app.form_values().items() if k not in ('action_1','action_2')},
                         {k:v for k,v in before.items() if k not in ('action_1','action_2')})

    def test_highlight_expires_after_three_seconds_and_keeps_swapped_values(self):
        app=self.app;old=app.values['action_1'].get();taken=app.values['action_2'].get()
        with patch.object(self.root,'after',wraps=self.root.after) as after:
            app.assign_binding('action_1',taken)
            after.assert_called_once();self.assertEqual(after.call_args.args[0],3000)
        deadline=time.monotonic()+3.6
        while app.swap_timers and time.monotonic()<deadline:self.root.update();time.sleep(.02)
        self.assertFalse(app.swap_timers)
        self.assertEqual(app.binding_entries['action_2'].cget('style'),'Binding.TEntry')
        self.assertEqual(app.values['action_1'].get(),taken)
        self.assertEqual(app.values['action_2'].get(),old)

    def test_movement_mouse_buttons_and_modified_wheel_bindings_swap(self):
        app=self.app
        for owner in ('forward','action_4','zoom_in'):
            before=app.form_values();app.assign_binding('action_1',before[owner])
            self.assertEqual(app.values[owner].get(),before['action_1'])
            self.assertEqual(app.values['action_1'].get(),before[owner])
            self.assertIn(owner,app.swap_timers)
            self.assertFalse(app.conflict_fields)

    def test_assigning_from_an_empty_field_clears_the_previous_owner(self):
        app=self.app;app.clear_binding('action_1');taken=app.values['action_2'].get()
        app.assign_binding('action_1',taken)
        self.assertEqual(app.values['action_2'].get(),'')
        self.assertIn('action_2',app.swap_timers)
        self.assertFalse(app.conflict_fields)

    def test_repeated_swaps_restart_the_timer_and_loading_another_preset_clears_it(self):
        app=self.app;app.assign_binding('action_1',app.values['action_2'].get())
        previous=app.swap_timers['action_2']
        app.assign_binding('action_3',app.values['action_2'].get())
        self.assertNotEqual(app.swap_timers['action_2'],previous)
        self.assertNotIn(previous,self.root.tk.call('after','info'))
        app.select('Mage')
        self.assertFalse(app.swap_timers)
        self.assertEqual(app.binding_entries['action_2'].cget('style'),'Binding.TEntry')

    def test_same_key_and_unused_key_do_not_swap_or_highlight_other_actions(self):
        app=self.app;before=app.form_values()
        app.assign_binding('action_1',before['action_1'])
        self.assertEqual(app.form_values(),before)
        self.assertFalse(app.swap_timers)
        app.assign_binding('action_1','Ctrl-Alt-F24')
        self.assertFalse(app.swap_timers)
        self.assertEqual(app.values['action_2'].get(),before['action_2'])

    def test_swapped_bindings_are_saved_and_imported_conflicts_still_turn_red(self):
        app=self.app;before=app.form_values()
        app.assign_binding('action_1',before['action_2']);app.update();app.load()
        self.assertEqual(app.values['action_1'].get(),before['action_2'])
        self.assertEqual(app.values['action_2'].get(),before['action_1'])
        self.assertFalse(app.swap_timers)
        duplicate=app.form_values();duplicate['action_1']=duplicate['action_2']
        app.fill(duplicate)
        self.assertIn('action_1',app.conflict_fields)
        self.assertIn('action_2',app.conflict_fields)
        self.assertEqual(app.binding_entries['action_1'].cget('style'),'Conflict.TEntry')


if __name__=='__main__':unittest.main()
