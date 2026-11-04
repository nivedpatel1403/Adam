"""Guest repair transport. Reads credentials from settings without printing them."""
import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adam.common.config import get_settings
from adam.sandbox.guest.http_channel import HTTPGuestChannel
from adam.sandbox.vbox.client import VirtualBoxClient


def channel():
    s = get_settings().http_guest
    return HTTPGuestChannel(base_url=s.base_url, capture_dir=s.capture_dir,
                            procmon_path=s.procmon_path, tshark_path=s.tshark_path,
                            sysmon_log=s.sysmon_log, tshark_interface=s.tshark_interface,
                            auth_token=s.auth_token)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ps')
    parser.add_argument('--ps-file', type=Path)
    parser.add_argument('--copy', type=Path)
    parser.add_argument('--destination')
    parser.add_argument('--vbox', action='store_true')
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    ch = channel()
    try:
        if args.copy:
            s = get_settings().sandbox
            r = await VirtualBoxClient().copy_to_guest(s.vm_name, guest_username=s.guest_username,
                 guest_password=s.guest_password, host_source_path=str(args.copy.resolve()),
                 guest_target_path=args.destination, timeout=args.timeout)
        else:
            ps = args.ps_file.read_text() if args.ps_file else args.ps
            argv = ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', base64.b64encode(ps.encode('utf-16le')).decode()]
            exe = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
            if args.vbox:
                s = get_settings().sandbox
                r = await VirtualBoxClient().run_in_guest(s.vm_name, guest_username=s.guest_username,
                    guest_password=s.guest_password, executable_path=exe, arguments=argv, timeout=args.timeout)
            else:
                r = await ch.run_process(exe, argv, wait=True, timeout_s=args.timeout)
        print(json.dumps({'return_code': r.return_code, 'stdout': r.stdout, 'stderr': r.stderr}, indent=2))
        return 0 if r.return_code == 0 else 1
    finally:
        await ch.aclose()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
