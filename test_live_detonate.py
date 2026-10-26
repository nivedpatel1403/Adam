import asyncio
import base64
import hashlib
import json
import httpx

async def main():
    headers = {"X-Adam-Token": "Adam_Sandbox_SecOps_2026!"}
    async with httpx.AsyncClient(base_url="http://192.168.19.102:8765", timeout=25.0, headers=headers) as client:
        # 1. Health
        r = await client.get("/health")
        print("1. /health:", r.status_code, r.json())

        # 2. Stage sample
        with open("samples/smoke_sample.exe", "rb") as f:
            data = f.read()
        sha256 = hashlib.sha256(data).hexdigest()
        content_b64 = base64.b64encode(data).decode("ascii")
        r = await client.post("/sample/upload", json={"filename": "smoke_sample.exe", "sha256": sha256, "content_base64": content_b64})
        print("2. /sample/upload:", r.status_code, r.json())

        # 3. Start process (detonation)
        r = await client.post("/process/start", json={"executable": "C:\\ADAM\\samples\\smoke_sample.exe", "wait": True, "timeout_s": 15.0})
        print("3. /process/start (detonation):", r.status_code, r.json())

        # 4. Live Mutation via apply_mutation pattern
        cmd = "Set-Content -Path 'C:\\ADAM\\mutation_marker.txt' -Value 'ADAM_LIVE_MUTATION_SUCCESS' -Force"
        r = await client.post("/process/start", json={
            "executable": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
            "arguments": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            "wait": True,
            "timeout_s": 15.0
        })
        print("4. /process/start (mutation):", r.status_code, r.json())

        # 5. Sysmon diagnostics
        r = await client.get("/sysmon/diagnostics")
        print("5. /sysmon/diagnostics:", r.status_code, r.json())

if __name__ == "__main__":
    asyncio.run(main())
