export function formatLocalTime(
  dtString: string | null | undefined,
  includeDate = true,
  includeMs = false
): string {
  if (!dtString) return 'N/A';
  try {
    const dt = new Date(dtString);
    if (isNaN(dt.getTime())) return dtString;

    const pad = (n: number) => String(n).padStart(2, '0');
    const yyyy = dt.getFullYear();
    const mm = pad(dt.getMonth() + 1);
    const dd = pad(dt.getDate());
    const hh = pad(dt.getHours());
    const min = pad(dt.getMinutes());
    const ss = pad(dt.getSeconds());
    const ms = String(dt.getMilliseconds()).padStart(3, '0');

    let timePart = `${hh}:${min}:${ss}`;
    if (includeMs) {
      timePart += `.${ms}`;
    }

    if (!includeDate) {
      return timePart;
    }

    return `${yyyy}-${mm}-${dd} ${timePart}`;
  } catch {
    return dtString;
  }
}

