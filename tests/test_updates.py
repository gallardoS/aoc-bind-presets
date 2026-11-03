from pathlib import Path
import hashlib
import io
import json
import struct
import tempfile
import unittest
from unittest.mock import Mock,patch
import app_updates as updates


def executable():
    data=bytearray(256);data[:2]=b'MZ';struct.pack_into('<I',data,60,128);data[128:134]=b'PE\0\0\x64\x86'
    return bytes(data)


def release(version='1.0.10',data=None):
    data=data or executable();digest=hashlib.sha256(data).hexdigest()
    return {'tag_name':'v'+version,'draft':False,'prerelease':False,'assets':[
        {'name':updates.ASSET_NAME,'state':'uploaded','size':len(data),'digest':'sha256:'+digest,
         'browser_download_url':f'https://github.com/{updates.REPOSITORY}/releases/download/v{version}/{updates.ASSET_NAME}'}]}


class UpdateTests(unittest.TestCase):
    def test_compares_versions_numerically_and_never_downgrades(self):
        self.assertIsNotNone(updates.find_update(release(),'1.0.9'))
        self.assertIsNone(updates.find_update(release(),'1.0.10'))
        self.assertIsNone(updates.find_update(release(),'1.1.0'))
        draft=release();draft['draft']=True
        self.assertIsNone(updates.find_update(draft,'1.0.0'))
        draft['draft']=False;draft['prerelease']=True
        self.assertIsNone(updates.find_update(draft,'1.0.0'))

    def test_only_uses_the_named_asset_from_this_repository(self):
        for url in ['http://github.com/gallardoS/aoc-bind-presets/releases/download/v1.0.10/AoC-bind-presets.exe',
                    'https://example.com/AoC-bind-presets.exe',
                    'https://github.com/other/repo/releases/download/v1.0.10/AoC-bind-presets.exe']:
            payload=release();payload['assets'][0]['browser_download_url']=url
            with self.assertRaises(ValueError):updates.find_update(payload,'1.0.0')
        payload=release();payload['assets'][0]['digest']=None
        with self.assertRaises(ValueError):updates.find_update(payload,'1.0.0')

    def test_download_is_checked_before_it_can_be_installed(self):
        data=executable();update=updates.find_update(release(data=data),'1.0.0')
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            path,digest=updates.download_update(update,folder,lambda url:io.BytesIO(data),lambda path:(1,0,10))
            self.assertEqual(path.read_bytes(),data)
            self.assertEqual(digest,hashlib.sha256(data).hexdigest())
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            bad=data[:-1]+b'X'
            with self.assertRaises(ValueError):updates.download_update(update,folder,lambda url:io.BytesIO(bad),lambda path:(1,0,10))
            self.assertFalse((folder/'candidate.exe').exists())

    def test_truncated_wrong_version_and_non_executables_are_rejected(self):
        data=executable();update=updates.find_update(release(data=data),'1.0.0')
        for payload,version in [(data[:-1],(1,0,10)),(data,(1,0,9)),(b'<html>not an exe</html>',(1,0,10))]:
            with tempfile.TemporaryDirectory() as temp,self.assertRaises(ValueError):
                candidate=update if payload[:2]==b'MZ' else updates.find_update(release(data=payload),'1.0.0')
                updates.download_update(candidate,Path(temp),lambda url:io.BytesIO(payload),lambda path:version)

    def test_checksum_file_can_verify_an_asset_without_a_digest(self):
        data=executable();payload=release(data=data);payload['assets'][0]['digest']=None
        checksum_url=f'https://github.com/{updates.REPOSITORY}/releases/download/v1.0.10/SHA256SUMS.txt'
        payload['assets'].append({'name':'SHA256SUMS.txt','state':'uploaded','browser_download_url':checksum_url})
        update=updates.find_update(payload,'1.0.0')
        checksum=(hashlib.sha256(data).hexdigest()+'  AoC-bind-presets.exe\n').encode()
        with tempfile.TemporaryDirectory() as temp:
            path,_=updates.download_update(update,Path(temp),lambda url:io.BytesIO(checksum if url==checksum_url else data),lambda path:(1,0,10))
            self.assertEqual(path.read_bytes(),data)

    def install_fixture(self,folder):
        target=folder/updates.ASSET_NAME;target.write_bytes(b'old executable')
        stage=folder/'.aoc-update-test';stage.mkdir();candidate=stage/'candidate.exe';candidate.write_bytes(executable())
        manifest={'owner':updates.OWNER,'target':str(target),'version':'1.0.10','sha256':updates.file_hash(candidate),
                  'original_sha256':updates.file_hash(target),'pids':[101,102]}
        path=stage/'manifest.json';path.write_text(json.dumps(manifest))
        data=folder/'data';data.mkdir();(data/'presets.json').write_bytes(b'personal presets')
        return target,stage,path

    def test_installer_waits_replaces_and_reopens_without_touching_presets(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);target,stage,manifest=self.install_fixture(folder)
            events=[]
            def wait(pids):self.assertEqual(pids,[101,102]);events.append('wait')
            def launch(path,work):self.assertEqual(path.read_bytes(),executable());events.append('launch')
            self.assertTrue(updates.install_update(manifest,wait,launch,lambda path:(1,0,10)))
            self.assertEqual(events,['wait','launch'])
            self.assertEqual((folder/'data'/'presets.json').read_bytes(),b'personal presets')

    def test_failed_launch_rolls_back_and_reopens_the_previous_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);target,stage,manifest=self.install_fixture(folder)
            attempts=[]
            def launch(path,work):
                attempts.append(path.read_bytes())
                if len(attempts)==1:raise OSError('Launch failed')
            self.assertFalse(updates.install_update(manifest,lambda pids:None,launch,lambda path:(1,0,10)))
            self.assertEqual(attempts,[executable(),b'old executable'])
            self.assertEqual(target.read_bytes(),b'old executable')
            self.assertEqual((folder/'data'/'presets.json').read_bytes(),b'personal presets')

    def test_installer_refuses_an_unrelated_target(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);target,stage,manifest=self.install_fixture(folder)
            payload=json.loads(manifest.read_text());payload['target']=str(folder/'ArenaOfChampions.exe');manifest.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):updates.install_update(manifest,lambda pids:None,Mock(),lambda path:(1,0,10))
            self.assertEqual(target.read_bytes(),b'old executable')

    def test_editing_draft_is_restored_after_an_update(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);draft={'name':'Mage','bindings':{'action_1':'Ctrl-KeyQ'},'priorities':{'action_1':3}}
            (folder/'update-draft.json').write_text(json.dumps(draft))
            app=Mock();app.data=folder;app.store={'presets':{'Mage':{}}}
            controller=updates.UpdateController(app);controller.idle=lambda:True
            controller.restore_draft()
            app.fill.assert_called_once_with(draft['bindings'],draft['priorities'])
            app.name.set.assert_called_once_with('Mage')
            self.assertFalse((folder/'update-draft.json').exists())

    def test_helper_is_a_separate_copy_and_receives_the_parent_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);target,stage,manifest=self.install_fixture(folder)
            with patch.object(updates.os,'getpid',return_value=101),patch.object(updates.os,'getppid',return_value=102),patch.object(updates.subprocess,'Popen') as launch:
                updates.prepare_install(target,stage,'1.0.10',updates.file_hash(stage/'candidate.exe'))
            payload=json.loads(manifest.read_text())
            self.assertEqual(payload['pids'],[101,102])
            self.assertEqual((stage/'update-helper.exe').read_bytes(),target.read_bytes())
            self.assertEqual(launch.call_args.args[0],[str(stage/'update-helper.exe'),'--install-update',str(manifest)])

    def test_update_waits_for_current_work_and_saves_the_draft_before_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);app=Mock();app.data=folder/'data';app.edit_order={'action_1':4}
            app.name.get.return_value='Mage';app.form_values.return_value={'action_1':'Ctrl-KeyQ'}
            app.busy=True
            controller=updates.UpdateController(app);controller.draft_restored=True
            controller.pending=(folder/'stage','1.0.10','digest')
            (folder/'stage').mkdir()
            controller.idle=lambda:not app.busy
            process=Mock();process.poll.return_value=None
            with patch.object(updates,'prepare_install',return_value=process) as prepare:
                controller.poll();prepare.assert_not_called();app.root.destroy.assert_not_called()
                app.busy=False;controller.poll();prepare.assert_called_once()
            draft=json.loads((app.data/'update-draft.json').read_text())
            self.assertEqual(draft['bindings'],{'action_1':'Ctrl-KeyQ'})
            self.assertEqual(draft['name'],'Mage')
            app.root.destroy.assert_not_called()
            (folder/'stage'/'helper-ready.json').write_text(json.dumps({'owner':updates.OWNER}))
            controller.finish_handoff()
            app.root.destroy.assert_called_once()


    def test_bundled_version_text_does_not_shadow_the_windows_dll(self):
        import sys
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'VERSION').write_text('1.0.6')
            original=updates.ctypes.WinDLL
            def frozen_loader(name,**kwargs):
                candidate=folder/Path(name).name
                return original(str(candidate) if candidate.is_file() else name,**kwargs)
            with patch.object(updates.ctypes,'WinDLL',side_effect=frozen_loader):
                self.assertGreater(updates.file_version(Path(sys.executable))[0],0)

    def test_download_reports_actual_bytes_and_verifies_before_finishing(self):
        data=executable();update=updates.find_update(release(data=data),'1.0.6');events=[]
        with tempfile.TemporaryDirectory() as temp:
            updates.download_update(update,Path(temp),lambda url:io.BytesIO(data),lambda path:(1,0,10),progress=lambda done,total:events.append((done,total)))
        self.assertEqual(events[-1],(len(data),len(data)))

    def test_progress_dialog_is_centered_and_does_not_block_installing(self):
        import presets
        with tempfile.TemporaryDirectory() as temp,patch.object(presets,'BASE',Path(temp)),patch.object(presets,'DEFAULT_SETTINGS',Path(__file__).parent/'fixtures'/'settings.ron'):
            root=presets.tk.Tk()
            try:
                app=presets.App(root);root.update()
                controller=updates.UpdateController(app)
                controller.progress_state=('1.0.8',42,'Downloading… 42%')
                with patch.object(controller,'idle',return_value=True):controller.show_progress()
                root.update()
                self.assertEqual(controller.progress_bar['value'],42)
                # Test the dialog exclusion without depending on keys held by
                # the person using the computer while the tests are running.
                with patch.object(updates.os,'name','posix'):self.assertTrue(controller.idle())
                self.assertLess(abs(controller.dialog.winfo_rootx()+controller.dialog.winfo_width()/2-root.winfo_rootx()-root.winfo_width()/2),20)
                controller.report_error('Download interrupted.')
                self.assertIsNone(controller.dialog)
                self.assertIn('Download interrupted.',app.status.get())
                self.assertIn('Download interrupted.',(app.data/'update-error.log').read_text())
            finally:root.destroy()


if __name__=='__main__':unittest.main(verbosity=2)
