import csv
import json
from datetime import UTC, datetime

import pytest

from adam.collectors.parsers.pcap import PcapParseError, parse_tshark_ek_line
from adam.collectors.parsers.pml import (
    ProcmonParseError,
    normalize_procmon_csv,
    parse_procmon_csv_row,
)


def test_actual_tshark_ek_field_names_preserve_http_endpoint():
    # Shape independently observed in Tshark 4.6.9's real `-T ek` export.
    packet = {"timestamp": "1793808144278", "layers": {
        "frame": {"frame_frame_number": "17", "frame_frame_time_epoch": "1793808144.2789037", "frame_frame_len": "150"},
        "ip": {"ip_ip_src": "192.168.19.102", "ip_ip_dst": "192.168.19.2"},
        "tcp": {"tcp_tcp_srcport": "49678", "tcp_tcp_dstport": "18080"},
        "http": {"http_http_host": "192.168.19.2:18080", "http_http_request_uri": "/unique",
                 "http_http_request_full_uri": "http://192.168.19.2:18080/unique"},
    }}
    event = parse_tshark_ek_line(json.dumps(packet), session_id="actual-format")
    assert event.source_event_id == 17
    assert event.attributes["network_endpoint"] == "http://192.168.19.2:18080/unique"
    assert event.attributes["dst_ip"] == "192.168.19.2"
    assert event.attributes["dst_port"] == "18080"


@pytest.mark.parametrize("timestamp", [
    "2026-11-04T18:53:38.310877900Z",
    "2026-11-05T00:23:38.310877900+05:30",
])
def test_real_ek_iso_timestamp_preserves_utc(timestamp):
    packet = {"layers": {"frame": {
        "frame_frame_number": "1", "frame_frame_time_epoch": timestamp,
    }}}
    event = parse_tshark_ek_line(json.dumps(packet), session_id="actual-iso-format")
    assert event.occurred_at == datetime(2026, 11, 4, 18, 53, 38, 310877, tzinfo=UTC)


def test_timezone_missing_packet_timestamp_is_rejected():
    packet = {"layers": {"frame": {
        "frame_frame_number": "1", "frame_frame_time_epoch": "2026-11-04T18:53:38",
    }}}
    with pytest.raises(PcapParseError, match="invalid frame.time_epoch"):
        parse_tshark_ek_line(json.dumps(packet), session_id="ambiguous-time")


def make_csv(path, times):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["Time of Day", "Process Name", "PID", "Operation", "Path", "Result", "Detail"])
        for time in times:
            writer.writerow([time, "verify.exe", "123", "CreateFile", r"C:\ADAM\marker", "SUCCESS", ""])


def test_time_only_procmon_rows_use_guest_date_timezone_and_midnight(tmp_path):
    raw, normalized = tmp_path / "raw.csv", tmp_path / "normalized.csv"
    make_csv(raw, ["23:59:59.1234567", "00:00:01.0000000"])
    normalize_procmon_csv(raw, normalized, started_at="2026-11-04T23:59:50-08:00", ended_at="2026-11-05T00:00:10-08:00")
    with normalized.open(newline="") as stream:
        events = [parse_procmon_csv_row(row, session_id="test", sequence=index) for index, row in enumerate(csv.DictReader(stream))]
    assert events[0].occurred_at == datetime(2026, 11, 5, 7, 59, 59, 123456, tzinfo=UTC)
    assert events[1].occurred_at == datetime(2026, 11, 5, 8, 0, 1, tzinfo=UTC)
    assert "Time of Day" in raw.read_text()


@pytest.mark.parametrize("start,end,time", [
    ("2026-11-04T08:00:00", "2026-11-04T08:01:00", "08:00:30"),
    ("2026-11-04T08:00:00-08:00", "2026-11-04T08:01:00-07:00", "08:00:30"),
    ("2026-11-04T08:00:00-08:00", "2026-11-04T08:01:00-08:00", "12:00:00"),
])
def test_ambiguous_or_outside_procmon_times_fail(tmp_path, start, end, time):
    raw = tmp_path / "raw.csv"
    make_csv(raw, [time])
    with pytest.raises(ProcmonParseError):
        normalize_procmon_csv(raw, tmp_path / "out.csv", started_at=start, ended_at=end)
