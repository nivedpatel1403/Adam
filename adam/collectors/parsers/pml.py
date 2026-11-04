"""
adam/collectors/parsers/pml.py

Parses Process Monitor (ProcMon) CSV export rows into
adam.contracts.raw_event.RawEvent. ARCHITECTURE.md section 5.3 /
docs/dev-a-environment-and-roadmap.md Phase 7 (file named `pml.py` after
ProcMon's native `.PML` capture format, per the roadmap's own naming --
see below for why this module parses CSV, not raw `.PML`).

Why CSV, not raw `.PML`. Section 5.3 itself describes this source as
"ProcMon (PML → CSV)": Process Monitor's own File > Save... export feature
converts its native binary `.PML` capture into CSV, and that CSV is this
parser's actual input. Parsing the raw `.PML` binary format directly would
require either Sysinternals' own (closed-source) tooling or a third-party
reverse-engineered library, neither of which is what the architecture
specifies -- the export-to-CSV step is assumed to already have happened
(via ProcMon itself, or a `procmon.exe /SaveAs` scripted export) before
this module runs, same division of labour as `evtx.py` assumes Sysmon has
already written its EVTX log.

Procmon's default "Time of Day" export carries no date. The HTTP collector
preserves the raw CSV and normalizes those values using the guest's actual
PML creation and export timestamps, including the guest UTC offset. Missing
or ambiguous capture context fails ingestion. Row parsing accepts normalized
timezone-aware ISO timestamps and the legacy US-locale "Date & Time" format.

Field-completeness note, same convention as evtx.py: ProcMon's default CSV
columns do not include a parent PID, full image path (only the bare
"Process Name"), command line, integrity level, or a process GUID. Where
`ProcessInfo` (adam/contracts/raw_event.py) requires a non-empty value this
parser cannot supply, the same `"-"` placeholder convention from evtx.py is
used, and `ppid` defaults to the same `0` sentinel. See evtx.py's module
docstring for the full reasoning; not repeated here to avoid drift between
two copies of the same explanation.
"""

from __future__ import annotations

import csv
import io
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from adam.contracts.enums import Category, Source
from adam.contracts.raw_event import ProcessInfo, RawEvent

_PLACEHOLDER = "-"

# ProcMon's default CSV export header, with "Time of Day" swapped for
# "Date & Time" per this module's required export configuration (see
# module docstring).
EXPECTED_HEADER = ("Date & Time", "Process Name", "PID", "Operation", "Path", "Result", "Detail")

# Curated ARCHITECTURE.md section 7.2 Category mapping for ProcMon's
# documented Operation names (https://learn.microsoft.com/sysinternals/
# downloads/procmon "Reference" section). Registry/network/process
# operations are enumerated explicitly; everything else defaults to FILE,
# since ProcMon's overwhelming majority of operations are filesystem I/O
# and enumerating every filesystem operation name individually would add
# risk of a typo silently mis-categorising a real record without adding
# real precision.
_PROCESS_OPERATIONS = {"Process Create", "Process Start", "Process Exit", "Thread Create", "Thread Exit"}
_MODULE_OPERATIONS = {"Load Image"}


def _categorize(operation: str) -> Category:
    if operation.startswith("Reg"):
        return Category.REGISTRY
    if operation.startswith(("TCP", "UDP")):
        return Category.NETWORK
    if operation in _PROCESS_OPERATIONS:
        return Category.PROCESS
    if operation in _MODULE_OPERATIONS:
        return Category.MODULE
    return Category.FILE


_DATETIME_RE = re.compile(
    r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<frac>\d+))?\s*(?P<ampm>[AP]M)?$"
)


class ProcmonParseError(Exception):
    """
    Raised when a ProcMon CSV row is too malformed to produce a RawEvent
    (wrong column count, unparseable "Date & Time", non-integer PID). Not
    yet folded into adam.common.errors' ParserError -- same disclosed,
    temporary status as evtx.py's SysmonParseError; see that class's
    docstring.
    """


def _parse_datetime(raw: str) -> datetime:
    """
    Parses ProcMon's "Date & Time" column in its standard US-locale format,
    e.g. "7/21/2026 2:32:11.4012207 PM". See module docstring for why this
    column (not the default "Time of Day") is required, and for the
    US-locale-only limitation.
    """
    if "T" in raw:
        try:
            value = datetime.fromisoformat(raw.strip())
            if value.tzinfo is None:
                raise ValueError("ISO timestamp lacks timezone")
            return value.astimezone(UTC)
        except ValueError as exc:
            raise ProcmonParseError(f"Invalid normalized timestamp: {raw!r}") from exc
    match = _DATETIME_RE.match(raw.strip())
    if match is None:
        raise ProcmonParseError(f"unrecognised ProcMon 'Date & Time' format: {raw!r}")

    hour = int(match.group("hour"))
    ampm = match.group("ampm")
    if ampm is not None:
        ampm = ampm.upper()
        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0

    frac = (match.group("frac") or "0").ljust(6, "0")[:6]  # pad/truncate to microseconds

    try:
        return datetime(
            year=int(match.group("year")),
            month=int(match.group("month")),
            day=int(match.group("day")),
            hour=hour,
            minute=int(match.group("minute")),
            second=int(match.group("second")),
            microsecond=int(frac),
            tzinfo=UTC,
        )
    except ValueError as exc:
        raise ProcmonParseError(f"invalid ProcMon 'Date & Time' value: {raw!r} ({exc})") from exc


def requires_time_context(path: str | Path) -> bool:
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        header = next(csv.reader(stream), [])
    return "Time of Day" in header and "Date & Time" not in header


def normalize_procmon_csv(source: Path, destination: Path, *, started_at: str, ended_at: str) -> None:
    """Resolve time-only rows using the guest's PML creation/export interval.

    Never infer a date or timezone from the host clock. Keep the raw export
    separately so every normalized timestamp can be independently reproduced.
    """
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(ended_at)
    if start.tzinfo is None or end.tzinfo is None or start.utcoffset() != end.utcoffset():
        raise ProcmonParseError("Missing or changed guest capture timezone")
    if end < start or end - start >= timedelta(days=1):
        raise ProcmonParseError("Capture interval cannot unambiguously resolve time-of-day rows")
    with source.open(encoding="utf-8-sig", newline="") as incoming:
        reader = csv.DictReader(incoming)
        fields = list(reader.fieldnames or [])
        if "Date & Time" in fields:
            shutil.copyfile(source, destination)
            return
        if "Time of Day" not in fields:
            raise ProcmonParseError("Procmon export has no time column")
        fields[fields.index("Time of Day")] = "Date & Time"
        with destination.open("w", encoding="utf-8", newline="") as outgoing:
            writer = csv.DictWriter(outgoing, fieldnames=fields)
            writer.writeheader()
            for row in reader:
                raw_time = row.pop("Time of Day")
                candidates = []
                for day_offset in (-1, 0, 1):
                    date = start.date() + timedelta(days=day_offset)
                    parsed = _parse_datetime(f"{date.month}/{date.day}/{date.year} {raw_time}")
                    local = parsed.replace(tzinfo=start.tzinfo)
                    if start - timedelta(seconds=10) <= local <= end + timedelta(seconds=10):
                        candidates.append(local)
                if len(candidates) != 1:
                    raise ProcmonParseError(f"Time-of-day row lies outside capture interval: {raw_time}")
                row["Date & Time"] = candidates[0].astimezone(UTC).isoformat()
                writer.writerow(row)


def parse_procmon_csv_row(
    row: dict[str, str],
    *,
    session_id: str,
    sequence: int = 0,
    raw_ref: str | None = None,
) -> RawEvent:
    """
    Parses one ProcMon CSV row (as a header-keyed dict, e.g. from
    `csv.DictReader`) into a RawEvent. Raises ProcmonParseError if a
    required column is missing or unparseable. See module docstring for
    the required column set and the "Date & Time" export requirement.
    """
    missing = [col for col in EXPECTED_HEADER if col not in row]
    if missing:
        raise ProcmonParseError(f"ProcMon CSV row missing required column(s): {missing}")

    occurred_at = _parse_datetime(row["Date & Time"])

    operation = row["Operation"].strip()
    category = _categorize(operation)

    pid_raw = row["PID"].strip()
    try:
        pid = int(pid_raw)
    except ValueError as exc:
        raise ProcmonParseError(f"non-integer PID: {pid_raw!r}") from exc

    process = ProcessInfo(
        pid=pid,
        ppid=0,  # not present in default ProcMon CSV columns -- see module docstring
        image=row["Process Name"].strip() or _PLACEHOLDER,
        command_line="",  # not present in default ProcMon CSV columns
        integrity_level=_PLACEHOLDER,  # not present in default ProcMon CSV columns
        user=_PLACEHOLDER,  # not present in default ProcMon CSV columns
        guid=_PLACEHOLDER,  # not present in default ProcMon CSV columns
    )

    return RawEvent(
        event_id=f"raw_procmon_{session_id}_{sequence}",
        session_id=session_id,
        source=Source.PROCMON,
        source_event_id=sequence,
        category=category,
        occurred_at=occurred_at,
        observed_at=datetime.now(UTC),
        process=process,
        attributes={
            "operation": operation,
            "path": row.get("Path", ""),
            "result": row.get("Result", ""),
            "detail": row.get("Detail", ""),
        },
        raw_ref=raw_ref,
    )


def parse_procmon_csv_text(
    csv_text: str,
    *,
    session_id: str,
    start_sequence: int = 0,
    raw_ref: str | None = None,
) -> list[RawEvent]:
    """
    Parses a complete ProcMon CSV export (including its header row) into a
    list of RawEvent, in file order. Convenience wrapper around
    parse_procmon_csv_row() for the common "parse the whole export at once"
    case; ProcmonCollector uses the row-level function directly so it can
    track its own read position across polls instead of re-parsing from
    the start of the file each time.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    events = []
    for i, row in enumerate(reader):
        events.append(
            parse_procmon_csv_row(row, session_id=session_id, sequence=start_sequence + i, raw_ref=raw_ref)
        )
    return events
