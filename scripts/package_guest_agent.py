#!/usr/bin/env python3
"""
scripts/package_guest_agent.py

Packages the ADAM guest agent for VM deployment:
1. Scans adam/sandbox/guest/agent/ for agent scripts and modules.
2. Computes SHA256 hashes and file sizes for all agent files.
3. Generates a deterministic build_id from the canonical sorted file hashes.
4. Writes build_manifest.json inside the agent directory.
5. Optionally creates a clean deployment zip archive.
6. Verifies existing manifests against deployed files (--verify).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT_FILES_REL = [
    "adam_agent.ps1",
    "install.ps1",
]


def compute_file_sha256(path: Path) -> str:
    """Computes the SHA256 hex digest of a file in 64 KiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def find_agent_files(agent_dir: Path) -> dict[str, Path]:
    """
    Finds all agent script and module files relative to agent_dir.
    Excludes existing build_manifest.json, temporary files, and directories.
    """
    files: dict[str, Path] = {}
    
    # Root scripts
    for rel_name in AGENT_FILES_REL:
        fpath = agent_dir / rel_name
        if fpath.is_file():
            files[rel_name.replace("\\", "/")] = fpath

    # Modules directory
    modules_dir = agent_dir / "modules"
    if modules_dir.is_dir():
        for psm1 in sorted(modules_dir.glob("*.psm1")):
            rel_name = f"modules/{psm1.name}"
            files[rel_name] = psm1

    return files


def generate_manifest(agent_dir: Path) -> dict[str, Any]:
    """
    Computes SHA256 hashes of all agent files and creates a build manifest.
    Computes a deterministic build_id over canonical sorted relative paths and hashes.
    """
    agent_files = find_agent_files(agent_dir)
    if not agent_files:
        raise ValueError(f"No agent files found in {agent_dir}")

    files_info: dict[str, dict[str, Any]] = {}
    composite_hasher = hashlib.sha256()

    for rel_path in sorted(agent_files.keys()):
        file_path = agent_files[rel_path]
        sha256_hex = compute_file_sha256(file_path)
        size_bytes = file_path.stat().st_size
        files_info[rel_path] = {
            "sha256": sha256_hex,
            "size_bytes": size_bytes,
        }
        composite_hasher.update(f"{rel_path}:{sha256_hex}:{size_bytes}\n".encode())

    build_id = composite_hasher.hexdigest()[:16]
    now_utc = datetime.now(timezone.utc).isoformat()

    manifest: dict[str, Any] = {
        "build_id": build_id,
        "created_utc": now_utc,
        "version": "1.0.0",
        "file_count": len(files_info),
        "files": files_info,
    }

    return manifest


def write_manifest(agent_dir: Path, manifest: dict[str, Any]) -> Path:
    """Writes build_manifest.json to the agent directory."""
    manifest_path = agent_dir / "build_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return manifest_path


def create_package_zip(agent_dir: Path, output_zip: Path, manifest: dict[str, Any]) -> Path:
    """Creates a zip archive containing agent files and build_manifest.json."""
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    agent_files = find_agent_files(agent_dir)

    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Add manifest
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        zf.writestr("build_manifest.json", manifest_bytes)

        # Add all agent files
        for rel_name, file_path in sorted(agent_files.items()):
            zf.write(file_path, arcname=rel_name)

    return output_zip


def verify_manifest(agent_dir: Path) -> bool:
    """
    Verifies that files in agent_dir match build_manifest.json.
    Returns True if valid, False if any hash or size mismatch.
    """
    manifest_path = agent_dir / "build_manifest.json"
    if not manifest_path.is_file():
        print(f"[!] Manifest not found at {manifest_path}", file=sys.stderr)
        return False

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    expected_files: dict[str, dict[str, Any]] = manifest.get("files", {})
    if not expected_files:
        print("[!] Manifest has no files entry", file=sys.stderr)
        return False

    composite_hasher = hashlib.sha256()
    for rel_path in sorted(expected_files.keys()):
        file_path = agent_dir / rel_path
        if not file_path.is_file():
            print(f"[FAIL] Missing file: {rel_path}", file=sys.stderr)
            return False

        expected_sha = expected_files[rel_path]["sha256"]
        actual_sha = compute_file_sha256(file_path)
        actual_size = file_path.stat().st_size

        if actual_sha != expected_sha:
            print(
                f"[FAIL] Checksum mismatch for {rel_path}: expected {expected_sha}, got {actual_sha}",
                file=sys.stderr,
            )
            return False

        composite_hasher.update(f"{rel_path}:{actual_sha}:{actual_size}\n".encode())

    computed_build_id = composite_hasher.hexdigest()[:16]
    expected_build_id = manifest.get("build_id")
    if computed_build_id != expected_build_id:
        print(
            f"[FAIL] Build ID mismatch: expected {expected_build_id}, computed {computed_build_id}",
            file=sys.stderr,
        )
        return False

    print(f"[OK] Agent manifest verified: build_id={computed_build_id} ({len(expected_files)} files)")
    return True


def package_agent(
    agent_dir: Path | None = None,
    output_zip: Path | None = None,
) -> dict[str, Any]:
    """High-level function to generate manifest and optionally package agent into a zip."""
    if agent_dir is None:
        agent_dir = Path(__file__).resolve().parents[1] / "adam" / "sandbox" / "guest" / "agent"

    manifest = generate_manifest(agent_dir)
    manifest_path = write_manifest(agent_dir, manifest)
    print(f"[OK] Generated {manifest_path} (build_id={manifest['build_id']})")

    if output_zip is not None:
        zip_path = create_package_zip(agent_dir, output_zip, manifest)
        print(f"[OK] Packaged agent to {zip_path} ({zip_path.stat().st_size} bytes)")

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Package and manifest ADAM guest agent")
    parser.add_argument(
        "--agent-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "adam" / "sandbox" / "guest" / "agent",
        help="Path to adam/sandbox/guest/agent directory",
    )
    parser.add_argument(
        "--output-zip",
        type=Path,
        default=None,
        help="Optional path to produce agent zip bundle",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify files in --agent-dir against build_manifest.json",
    )

    args = parser.parse_args()

    if args.verify:
        success = verify_manifest(args.agent_dir)
        return 0 if success else 1

    try:
        manifest = package_agent(args.agent_dir, args.output_zip)
        print(f"Build ID: {manifest['build_id']}")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Error packaging guest agent: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
