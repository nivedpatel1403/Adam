import React, { useState } from 'react';
import { Button } from '../design/Button';
import { fetchSessionReport, getArtifactDownloadUrl } from '../../api/client';

interface Props {
  sessionId: string;
}

export const DownloadBar: React.FC<Props> = ({ sessionId }) => {
  const [downloading, setDownloading] = useState<string | null>(null);

  const handleDownload = async (format: 'html' | 'md' | 'json') => {
    try {
      setDownloading(format);
      const data = await fetchSessionReport(sessionId, format);
      const content = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
      const mime =
        format === 'html'
          ? 'text/html'
          : format === 'md'
          ? 'text/markdown'
          : 'application/json';

      const blob = new Blob([content], { type: mime });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `report_${sessionId}.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err: any) {
      alert(`Report download error: ${err.message}`);
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '8px',
        alignItems: 'center',
        padding: '12px 16px',
        background: 'var(--dp-color-plate)',
        border: '1px solid var(--dp-color-line)',
        marginBottom: '24px',
      }}
    >
      <span
        style={{
          fontSize: '11px',
          color: 'var(--dp-color-ink-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          marginRight: '6px',
        }}
      >
        Export Intelligence:
      </span>

      <Button
        size="sm"
        disabled={downloading !== null}
        onClick={() => handleDownload('html')}
      >
        {downloading === 'html' ? 'EXPORTING...' : '📥 HTML REPORT'}
      </Button>

      <Button
        size="sm"
        disabled={downloading !== null}
        onClick={() => handleDownload('md')}
      >
        {downloading === 'md' ? 'EXPORTING...' : '📥 MARKDOWN'}
      </Button>

      <Button
        size="sm"
        disabled={downloading !== null}
        onClick={() => handleDownload('json')}
      >
        {downloading === 'json' ? 'EXPORTING...' : '📥 JSON'}
      </Button>

      <a
        href={getArtifactDownloadUrl(sessionId, 'raw')}
        download
        style={{ textDecoration: 'none' }}
      >
        <Button size="sm" variant="ghost">
          RAW.JSONL
        </Button>
      </a>

      <a
        href={getArtifactDownloadUrl(sessionId, 'sysmon')}
        download
        style={{ textDecoration: 'none' }}
      >
        <Button size="sm" variant="ghost">
          SYSMON.EVTX
        </Button>
      </a>
    </div>
  );
};

