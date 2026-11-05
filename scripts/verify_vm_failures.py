"""Exercise controlled failures through the real CLI, Runner and Windows VM.

Faults are scoped to this process's channel instances. No service is disabled,
and every run restores the explicitly selected candidate through normal cleanup.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aiosqlite
import httpx
from typer.testing import CliRunner

from adam.cli import run as cli_run
from adam.cli.main import app
from adam.common.config import get_settings
from adam.db.repositories.sqlite import SQLiteSessionRepository
from adam.orchestrator.runner import Runner
from adam.sandbox.vbox.client import VirtualBoxClient
from scripts.verify_candidate_snapshots import VerificationHTTPHandler


def compile_sample(output: Path, *, sleeper: bool = False) -> None:
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if sleeper:
        source = output.with_suffix('.cs')
        source.write_text('using System.Threading; class Sleep { static void Main() { Thread.Sleep(60000); } }')
    else:
        source = ROOT / 'scripts/verification_workload.cs'
    subprocess.run([str(compiler), '/nologo', f'/out:{output}', str(source)], check=True, capture_output=True)


async def read_session(settings, session_id):
    async with aiosqlite.connect(settings.db.path) as connection:
        return await SQLiteSessionRepository(connection).get_by_id(session_id)


def run_case(case: str, candidate: str, output: Path, ready_timeout_s: float = 900) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    sample = output / ('ADAM_VERIFY_CYCLE_1_' + case + '.exe')
    compile_sample(sample, sleeper=case == 'timeout')
    sentinel = output / 'ADAM_Verification_Unrelated_Sentinel.exe'
    if case == 'timeout':
        compile_sample(sentinel, sleeper=True)
    settings = get_settings().model_copy(deep=True)
    settings.sandbox.snapshot_name = candidate
    settings.sandbox.guest_ready_timeout_s = ready_timeout_s
    settings.http_guest.tools_required = True
    settings.http_guest.tshark_interface = 'auto'
    settings.live.enabled = True
    settings.network.host = settings.network.host_ip = '192.168.19.2'
    if case == 'missing_tool':
        settings.http_guest.procmon_path = r'C:\ADAM\Tools\deliberately_missing.exe'
    evidence = {'case': case, 'candidate_uuid': candidate, 'success': False, 'fault_hits': 0}

    class FaultRunner(Runner):
        def __init__(self):
            super().__init__(settings)

        def _build_guest_channel(self, *args, **kwargs):
            runtime = super()._build_guest_channel(*args, **kwargs)
            channel = runtime.channel
            original_request = channel._request
            original_run = channel.run_process
            original_export = channel.stop_export_and_fetch
            sentinel_pid = None

            async def fault_request(method, path, **request_kwargs):
                if (case == 'polling' and path == '/sysmon/events/since'
                        and request_kwargs.get('params', {}).get('record_id', -1) >= 0):
                    evidence['fault_hits'] += 1
                    logging.getLogger(__name__).warning('Controlled unavailable polling endpoint')
                    return None
                if case == 'export' and path == '/procmon/export':
                    evidence['fault_hits'] += 1
                    logging.getLogger(__name__).warning('Controlled Procmon export failure')
                    return None
                return await original_request(method, path, **request_kwargs)

            async def run_process(executable_path, arguments=None, **run_kwargs):
                nonlocal sentinel_pid
                result = await original_run(executable_path, arguments, **run_kwargs)
                if case == 'timeout' and str(executable_path).endswith(sample.name) and not run_kwargs.get('wait', True):
                    evidence['workload_pid'] = result.pid
                    destination = 'C:\\ADAM\\samples\\' + sentinel.name
                    await channel.stage_sample(sentinel, destination)
                    unrelated = await original_run(destination, wait=False)
                    sentinel_pid = unrelated.pid
                    evidence['sentinel_pid'] = sentinel_pid
                    evidence['fault_hits'] += 1
                return result

            async def export_and_check(*export_args, **export_kwargs):
                if sentinel_pid is not None:
                    evidence['sentinel_survived_workload_timeout'] = bool(await channel.query_processes(pid=sentinel_pid))
                    evidence['workload_stopped_before_export'] = not bool(await channel.query_processes(pid=evidence['workload_pid']))
                    await channel.terminate_process(pid=sentinel_pid)
                return await original_export(*export_args, **export_kwargs)

            channel._request = fault_request
            channel.run_process = run_process
            channel.stop_export_and_fetch = export_and_check
            return runtime

    original_factory = cli_run.Runner
    cli_run.Runner = FaultRunner
    try:
        result = CliRunner().invoke(app, ['run', str(sample.resolve()), '--arm', 'control',
            '--timeout-seconds', '8' if case == 'timeout' else '90',
            '--artifacts-dir', str(output.resolve())])
    finally:
        cli_run.Runner = original_factory
    (output / 'cli-output.txt').write_text(result.output, encoding='utf-8')
    evidence['cli_exit_code'] = result.exit_code
    if result.exception is not None:
        evidence['cli_exception'] = repr(result.exception)
    sessions = list(output.glob('sess_*/session.json'))
    # Runner writes session metadata alongside raw artifacts after teardown.
    if not sessions:
        evidence['error'] = 'No final session artifact'
        (output / 'result.json').write_text(json.dumps(evidence, indent=2))
        return evidence
    artifact_session = json.loads(sessions[0].read_text())
    session = asyncio.run(read_session(settings, artifact_session['session_id']))
    evidence['session'] = session.model_dump(mode='json')
    evidence['database_matches_artifact'] = session.model_dump(mode='json') == artifact_session
    for suffix in ('', '/events', '/decisions', '/mutations', '/report'):
        response = httpx.get('http://127.0.0.1:8000/api/v1/sessions/' + session.session_id + suffix, timeout=120)
        response.raise_for_status()
        name = suffix.strip('/') or 'session'
        (output / ('api-' + name + '.json')).write_text(json.dumps(response.json(), indent=2), encoding='utf-8')
    api = json.loads((output / 'api-session.json').read_text())['metadata']
    report = json.loads((output / 'api-report.json').read_text())
    evidence['api_report_agree'] = api['status'] == report['status'] == session.status.value
    evidence['vm_state'] = asyncio.run(VirtualBoxClient().get_state(settings.sandbox.vm_name))
    evidence['partial_artifacts_retained'] = (sessions[0].parent / 'raw.jsonl').exists()
    evidence['success'] = bool(result.exit_code in (1, 2) and session.status.value in ('FAILED', 'PARTIAL')
        and session.failures and evidence['database_matches_artifact'] and evidence['api_report_agree']
        and evidence['vm_state'] == 'poweroff' and evidence['partial_artifacts_retained']
        and not any(f.stage == 'teardown' for f in session.failures)
        and (case == 'missing_tool' or evidence['fault_hits'] > 0))
    if case == 'timeout':
        evidence['success'] = evidence['success'] and evidence.get('sentinel_survived_workload_timeout') and evidence.get('workload_stopped_before_export')
    (output / 'result.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--case', choices=['missing_tool', 'polling', 'timeout', 'export'])
    parser.add_argument('--guest-ready-timeout', type=float, default=900)
    args = parser.parse_args()
    if not 0 < args.guest_ready_timeout <= 3600:
        parser.error('--guest-ready-timeout must be greater than zero and at most 3600 seconds')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.WARNING, filename=args.output_dir / 'failures.log')
    server = ThreadingHTTPServer(('192.168.19.2', 18080), VerificationHTTPHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    results = []
    try:
        for case in ([args.case] if args.case else ['missing_tool', 'polling', 'timeout', 'export']):
            result = run_case(case, args.candidate, args.output_dir / case, args.guest_ready_timeout)
            results.append(result)
            print(json.dumps({'case':case,'success':result['success'],'session_id':result.get('session',{}).get('session_id')}), flush=True)
            if not result['success']:
                break
    finally:
        server.shutdown()
        server.server_close()
    (args.output_dir / 'result.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    return 0 if results and all(result['success'] for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
