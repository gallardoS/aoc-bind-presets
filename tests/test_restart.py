from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch, Mock
import presets
from restart_game import apply_with_restart


FIXTURE = Path(__file__).parent / 'fixtures' / 'settings.ron'


@patch.object(presets, 'DEFAULT_SETTINGS', FIXTURE)
class RestartTests(unittest.TestCase):
    def test_close_apply_launch_in_that_order(self):
        events = []
        backend = Mock()
        backend.close.side_effect = lambda game: events.append('close')
        backend.launch.side_effect = lambda game: events.append('launch')
        def apply(*args): events.append('apply'); return Path('backup.ron')
        result = apply_with_restart(Path('settings.ron'),{},Path('backups'),{'pid':1},apply,backend)
        self.assertEqual(events,['close','apply','launch'])
        self.assertEqual(result,(Path('backup.ron'),True))

    def test_closed_game_is_not_launched(self):
        backend = Mock(); apply = Mock(return_value=Path('backup.ron'))
        self.assertEqual(apply_with_restart(Path('settings.ron'),{},Path('backups'),None,apply,backend),(Path('backup.ron'),False))
        backend.close.assert_not_called(); backend.launch.assert_not_called()

    def test_failed_apply_reopens_game_and_reports_failure(self):
        backend = Mock(); apply = Mock(side_effect=ValueError('Server unavailable'))
        with self.assertRaisesRegex(ValueError,'game was reopened'):
            apply_with_restart(Path('settings.ron'),{},Path('backups'),{'pid':1},apply,backend)
        backend.launch.assert_called_once()

    def test_failed_close_never_applies_or_launches(self):
        backend = Mock(); backend.close.side_effect = ValueError('Still running'); apply = Mock()
        with self.assertRaises(ValueError):
            apply_with_restart(Path('settings.ron'),{},Path('backups'),{'pid':1},apply,backend)
        apply.assert_not_called(); backend.launch.assert_not_called()

    def test_failed_launch_reports_that_preset_was_saved(self):
        backend = Mock(); backend.launch.side_effect = OSError('Missing executable')
        with self.assertRaisesRegex(ValueError,'preset was applied'):
            apply_with_restart(Path('settings.ron'),{},Path('backups'),{'pid':1},Mock(),backend)

    def test_popup_requires_consent_and_cancel_leaves_game_alone(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(presets,'BASE',Path(temp)):
            root = tk.Tk()
            try:
                app = presets.App(root); root.update()
                game = {'pid':123,'path':'ArenaOfChampions.exe','created':456}
                with patch.object(presets,'running_game',return_value=game), patch.object(presets,'apply_with_restart') as apply:
                    app.apply(); root.update()
                    dialog = app.restart_dialog
                    self.assertIsNotNone(dialog)
                    def buttons(widget):
                        result = []
                        for child in widget.winfo_children():
                            if isinstance(child,presets.ttk.Button): result.append(child)
                            result.extend(buttons(child))
                        return result
                    actions = {button.cget('text'):button for button in buttons(dialog)}
                    self.assertEqual(set(actions),{'Cancel','Restart and apply'})
                    apply.assert_not_called()
                    actions['Cancel'].invoke(); root.update()
                    self.assertIsNone(app.restart_dialog)
                    apply.assert_not_called()
                    app.apply(); root.update()
                    actions = {button.cget('text'):button for button in buttons(app.restart_dialog)}
                    with patch.object(app,'start_apply') as start:
                        actions['Restart and apply'].invoke()
                        start.assert_called_once_with(app.form_values(),False,game,approved=True)
                    apply.assert_not_called()
                    texts = [button.cget('text') for button in buttons(root)]
                    self.assertIn('Apply preset',texts)
            finally: root.destroy()


if __name__=='__main__': unittest.main(verbosity=2)
