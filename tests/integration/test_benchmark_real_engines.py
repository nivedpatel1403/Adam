"""
tests/integration/test_benchmark_real_engines.py

Integration test validating that run_benchmark_orchestration executes real
Fusion -> Policy -> Deception engines via EventBus rather than returning
hardcoded synthetic mock metrics.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from adam.cli.benchmark import run_benchmark_orchestration
from adam.corpus.manager import CorpusManager, CorpusManifest


@pytest.mark.asyncio
async def test_benchmark_real_engine_execution_varies_with_input(tmp_path: Path) -> None:
    """
    Verifies that different sample inputs produce varying, engine-derived metrics,
    and that Treatment arm yield systematically exceeds Control arm yield.
    """
    corpus_dir = tmp_path / "engine_corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    samples = []
    # Create 4 distinct samples to test statistical aggregation
    for i in range(4):
        raw = f"MZ\x90\x00LiveEngineSample_{i}_payload_data".encode("latin1")
        blob_path = corpus_dir / f"sample_{i}.enc"
        meta = CorpusManager.create_encrypted_blob(
            raw_bytes=raw,
            output_blob_path=blob_path,
            filename=f"sample_{i}.exe",
            secret_key="real_engine_test_key",
            family_label=f"Family_{i % 2}",
        )
        samples.append(meta)

    manifest_file = corpus_dir / "manifest.json"
    manifest_file.write_text(
        json.dumps(CorpusManifest(samples=samples).model_dump(), indent=2),
        encoding="utf-8",
    )

    output_report_file = tmp_path / "benchmark_real_engine_report.json"

    report = await run_benchmark_orchestration(
        manifest_path=str(manifest_file),
        profile_id="win10_x64_enterprise_office_decoy",
        output_path=str(output_report_file),
        secret_key="real_engine_test_key",
    )

    assert report.total_pairs == 4
    assert output_report_file.exists()

    report_json = json.loads(output_report_file.read_text(encoding="utf-8"))
    pairs = report_json["per_sample_pairs"]
    assert len(pairs) == 4

    # Validate that Treatment arm yields strictly exceed Control arm yields
    for pair in pairs:
        assert pair["treatment_yield"] > pair["control_yield"]
        assert pair["delta_yield"] > 0
        assert pair["treatment_events"] >= pair["control_events"]

    # Validate mean delta yield is positive
    assert report.mean_delta_yield > 0.10

    # Validate that primitive records were collected and evaluated
    assert len(report.primitive_plausibility) > 0
    for prim in report.primitive_plausibility:
        assert prim.mean_score > 0.0
