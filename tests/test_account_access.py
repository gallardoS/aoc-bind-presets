import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

import presets
from restart_game import apply_with_restart


class AccountAccessTests(unittest.TestCase):
    def test_get_and_put_identify_the_manager_and_keep_bearer_authentication(self):
        for method, payload in [('GET', None), ('PUT', {'settings': '(keys:())'})]:
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock()
            response.read.return_value=b'{"settings":"saved"}'
            with patch.object(presets.urllib.request,'urlopen',return_value=response) as request,patch.object(presets,'current_version',return_value='1.0.10'):
                self.assertEqual(presets.account_api(method,payload,'fake-token'),{'settings':'saved'})
            sent=request.call_args.args[0]
            self.assertEqual(sent.get_header('Authorization'),'Bearer fake-token')
            self.assertEqual(sent.get_header('User-agent'),'AoC-bind-presets/1.0.10 (+https://github.com/gallardoS/aoc-bind-presets)')
            self.assertEqual(sent.method,method)
            if payload:self.assertEqual(json.loads(sent.data),payload)

    def test_cloudflare_and_other_forbidden_responses_are_not_expired_sessions(self):
        for code,body,headers,expected in [
            (403,b'{"cloudflare_error":true,"error_code":1010}',{},'Cloudflare'),
            (403,b'<html>Attention Required! Cloudflare</html>',{},'Cloudflare'),
            (403,b'Please wait',{'cf-mitigated':'challenge'},'Cloudflare'),
            (403,b'{"error":"forbidden"}',{},'denied access'),
            (401,b'{"error":"expired"}',{},'session has expired'),
            (500,b'Server error',{},'HTTP 500'),
        ]:
            error=urllib.error.HTTPError('https://arenaofchampions.live/api/settings',code,'Denied',headers,io.BytesIO(body))
            with self.subTest(code=code,body=body),patch.object(presets.urllib.request,'urlopen',side_effect=error):
                with self.assertRaisesRegex(ValueError,expected) as caught:presets.account_api('GET',None,'fake-token')
                if code==403:self.assertNotIn('Your game session has expired',str(caught.exception))
                self.assertNotIn('fake-token',str(caught.exception))

    def test_failed_preflight_leaves_running_game_and_files_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);path=folder/'settings.ron';path.write_bytes(b'original settings')
            backend=Mock();apply=Mock();check=Mock(side_effect=ValueError('Cloudflare blocked access'))
            with self.assertRaisesRegex(ValueError,'Cloudflare'):
                apply_with_restart(path,{},folder/'backups',{'pid':123},apply,backend,preflight=check)
            backend.close.assert_not_called();backend.launch.assert_not_called();apply.assert_not_called()
            self.assertEqual(path.read_bytes(),b'original settings')
            self.assertFalse((folder/'backups').exists())

    def test_preflight_checks_access_before_close_and_apply_still_reads_fresh_settings(self):
        order=[];backend=Mock()
        backend.close.side_effect=lambda game:order.append('close')
        backend.launch.side_effect=lambda game:order.append('launch')
        def check(path):order.append('check')
        def apply(*args):order.append('apply');return Path('backup.ron')
        result=apply_with_restart(Path('settings.ron'),{},Path('backups'),{'pid':123},apply,backend,preflight=check)
        self.assertEqual(order,['check','close','apply','launch'])
        self.assertEqual(result,(Path('backup.ron'),True))

    def test_access_check_uses_the_current_saved_session_and_never_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);path=folder/'settings.ron';path.write_bytes(b'original settings')
            (folder/'session.ron').write_text('(token:"fake-current-session")')
            api=Mock(return_value={'settings':'(keys:())'})
            with patch.object(presets,'account_api',api):presets.check_account_access(path)
            api.assert_called_once_with('GET',None,'fake-current-session')
            self.assertEqual(path.read_bytes(),b'original settings')


if __name__=='__main__':unittest.main()
