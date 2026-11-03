import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock,patch

import app_updates as updates
from update_installer import InstallerWindow


class UpdateHandoffTests(unittest.TestCase):
    def fixture(self,folder):
        target=folder/updates.ASSET_NAME;target.write_bytes(b'old executable')
        stage=folder/'.aoc-update-test';stage.mkdir()
        payload={'owner':updates.OWNER,'target':str(target),'version':'1.0.10','pids':[],
                 'sha256':'unused','original_sha256':'unused','handoff':1}
        manifest=stage/'manifest.json';manifest.write_text(json.dumps(payload))
        return target,stage,manifest

    def test_installer_window_stays_open_after_launch_until_manager_confirms(self):
        with tempfile.TemporaryDirectory() as temp:
            target,stage,manifest=self.fixture(Path(temp));root=tk.Tk()
            try:
                install=Mock(return_value=True)
                window=InstallerWindow(root,manifest,installer=install,visible=lambda path:False)
                root.update();window.start()
                self.assertTrue((stage/'helper-ready.json').exists())
                deadline=time.monotonic()+2
                while not window.installed and time.monotonic()<deadline:root.update();time.sleep(.01)
                self.assertTrue(window.installed)
                self.assertTrue(root.winfo_viewable())
                self.assertFalse((stage/'helper-done.json').exists())
                (stage/'manager-ready.json').write_text(json.dumps({'owner':updates.OWNER}))
                window.poll()
                self.assertTrue((stage/'helper-done.json').exists())
                self.assertEqual(target.read_bytes(),b'old executable')
                install.assert_called_once()
            finally:
                try:root.destroy()
                except tk.TclError:pass

    def test_older_manager_can_confirm_by_its_visible_window(self):
        with tempfile.TemporaryDirectory() as temp:
            target,stage,manifest=self.fixture(Path(temp));root=tk.Tk()
            try:
                visible=Mock(return_value=True)
                window=InstallerWindow(root,manifest,visible=visible)
                window.installed=True;window.deadline=time.monotonic()+90
                window.poll()
                visible.assert_called_once_with(target.resolve())
                self.assertTrue((stage/'helper-done.json').exists())
            finally:
                try:root.destroy()
                except tk.TclError:pass

    def test_startup_timeout_keeps_an_error_window_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            _,stage,manifest=self.fixture(Path(temp));root=tk.Tk()
            try:
                window=InstallerWindow(root,manifest,visible=lambda path:False)
                root.update();window.installed=True;window.deadline=0;window.poll();root.update()
                self.assertTrue(window.failed)
                self.assertTrue(root.winfo_viewable())
                self.assertFalse((stage/'helper-done.json').exists())
                self.assertIn('manually',window.detail.cget('text'))
            finally:root.destroy()

    def test_cleanup_waits_for_the_independent_window_to_finish(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);target,stage,manifest=self.fixture(folder)
            for name in ('candidate.exe','update-helper.exe','helper-ready.json','manager-ready.json'):(stage/name).write_text('test')
            self.assertFalse(updates.cleanup_stage(stage,folder))
            self.assertTrue((stage/'update-helper.exe').exists())
            (stage/'helper-done.json').write_text(json.dumps({'owner':updates.OWNER}))
            self.assertTrue(updates.cleanup_stage(stage,folder))
            self.assertFalse(stage.exists())
            self.assertEqual(target.read_bytes(),b'old executable')

    def test_new_manager_confirms_only_after_its_window_and_draft_are_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);_,stage,_=self.fixture(folder)
            app=Mock();app.data=folder/'data';app.busy=False
            app.root.winfo_viewable.return_value=False
            controller=updates.UpdateController(app);controller.draft_restored=True
            controller.confirm_open(stage)
            self.assertFalse((stage/'manager-ready.json').exists())
            app.root.winfo_viewable.return_value=True;controller.draft_restored=False
            controller.confirm_open(stage)
            self.assertFalse((stage/'manager-ready.json').exists())
            controller.draft_restored=True;controller.confirm_open(stage)
            self.assertEqual(json.loads((stage/'manager-ready.json').read_text()),{'owner':updates.OWNER})

    def test_failed_helper_start_keeps_the_original_manager_open(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);_,stage,_=self.fixture(folder)
            app=Mock();app.data=folder/'data';app.data.mkdir()
            (app.data/'update-draft.json').write_text('{}')
            process=Mock();process.poll.return_value=1
            controller=updates.UpdateController(app)
            controller.handoff=(stage,process,time.monotonic()+30)
            controller.finish_handoff()
            app.root.destroy.assert_not_called()
            self.assertFalse(app.busy)
            self.assertTrue((stage/'cancel-update.json').exists())
            self.assertFalse((app.data/'update-draft.json').exists())


if __name__=='__main__':unittest.main()
