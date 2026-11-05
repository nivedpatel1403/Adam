import { useEffect, useRef, useState } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Check, Copy, AlertCircle, LoaderCircle, X } from 'lucide-react';

export function Button({ children, primary, compact, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean; compact?: boolean }) {
  return <button type="button" className={`key ${primary ? 'key-primary' : ''} ${compact ? 'key-compact' : ''} ${className}`} {...props}>{children}</button>;
}
export function Panel({ title, eyebrow, action, children, className = '' }: { title?: string; eyebrow?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>
    {(title || action) && <div className="panel-heading"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}{title && <h2>{title}</h2>}</div>{action}</div>}
    {children}
  </section>;
}
export function Status({ value }: { value: string }) {
  const label = value.toLowerCase().replaceAll('_', ' ');
  return <span className={`status status-${value.toLowerCase()}`}><span className="status-dot" aria-hidden="true" />{label.charAt(0).toUpperCase() + label.slice(1)}</span>;
}
export function Notice({ children, error = false, action }: { children: ReactNode; error?: boolean; action?: ReactNode }) {
  return <div className={`notice ${error ? 'notice-error' : ''}`} role={error ? 'alert' : 'status'}><AlertCircle size={16} aria-hidden="true"/><div>{children}</div>{action}</div>;
}
export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{children && <p>{children}</p>}{action}</div>;
}
export function Loading({ text = 'Loading records…' }: { text?: string }) {
  return <div className="loading" role="status"><LoaderCircle size={17} aria-hidden="true"/>{text}</div>;
}
export function CopyValue({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => { if (copied) { const timer = setTimeout(() => setCopied(false), 1800); return () => clearTimeout(timer); } }, [copied]);
  return <div className="copy-value"><code title={value}>{value}</code><Button compact aria-label="Copy full SHA-256 hash" onClick={async () => { try { await navigator.clipboard.writeText(value); setCopied(true); setError(false); } catch { setError(true); } }}>{copied ? <Check size={15}/> : <Copy size={15}/>}</Button><span className="sr-only" role="status">{copied ? 'Hash copied' : error ? 'Unable to copy. Select the hash manually.' : ''}</span></div>;
}
export function Dialog({ open, title, children, onClose }: { open: boolean; title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = ref.current;
    if (open && !dialog?.open) dialog?.showModal();
    if (!open && dialog?.open) dialog?.close();
    return () => { if (open) { dialog?.close(); previous?.focus(); } };
  }, [open]);
  return <dialog ref={ref} aria-label={title} onCancel={onClose} onClose={onClose} className="dialog"><div className="panel-heading"><h2>{title}</h2><Button compact onClick={onClose} aria-label="Close dialog" autoFocus><X size={17}/></Button></div>{children}</dialog>;
}
