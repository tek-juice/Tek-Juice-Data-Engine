/**
 * copyToClipboard
 *
 * Safe clipboard write that works in both secure (HTTPS) and insecure (HTTP)
 * browser contexts.
 *
 * navigator.clipboard is only available when the page is served over HTTPS or
 * from localhost.  On plain HTTP (e.g. http://54.86.109.228:9601) the property
 * is undefined, causing an immediate TypeError if called directly.
 *
 * Usage:
 *   import { copyToClipboard } from '../../utils/clipboard';
 *   const ok = await copyToClipboard(text);   // true on success, false on failure
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  // Prefer the modern async Clipboard API (requires secure context).
  if (typeof navigator !== 'undefined' && navigator.clipboard != null && 'writeText' in navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to legacy approach.
    }
  }

  // Legacy fallback: works on plain HTTP and older browsers.
  try {
    const el = document.createElement('textarea');
    el.value = text;
    // Keep it out of the visible viewport.
    el.style.position = 'fixed';
    el.style.top = '-9999px';
    el.style.left = '-9999px';
    el.style.opacity = '0';
    document.body.appendChild(el);
    el.focus();
    el.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(el);
    return ok;
  } catch {
    return false;
  }
}
