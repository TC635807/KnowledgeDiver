// Lightweight, dependency-free Markdown parser for basic rendering
// - Supports headings (# to ######)
// - Bold (**text**), Italic (*text*)
// - Inline code (`code`)
// - Links [text](url)
// - Simple [[card-id]] link syntax
export function sanitizeUrl(url: string): string {
  // Allow http:, https:, mailto:, relative paths (starting with / or . or #)
  // Reject everything else (javascript:, data:, vbscript:, etc.)
  if (!url) return '#';
  try {
    const u = new URL(url, window.location.origin);
    if (['http:', 'https:', 'mailto:'].includes(u.protocol)) return url;
  } catch {
    // If URL constructor throws, it might be a relative path
    if (/^[#./]/.test(url)) return url;
  }
  return '#';
}

export function extractLinks(content: string): string[] {
  const re = /\[\[([^\]]+)\]\]/g;
  const matches: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    matches.push(m[1]);
  }
  return matches;
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function inlineReplace(text: string): string {
  // Bold
  let t = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  // Italic (avoid intercepting when bold already processed)
  t = t.replace(/\*(?!\*)(.*?)\*/g, '<em>$1</em>');
  // Inline code
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Markdown links
  t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, text, url) => '<a href="' + sanitizeUrl(url) + '" target="_blank" rel="noopener">' + text + '</a>');
  // Card link syntax
  t = t.replace(/\[\[([^\]]+)\]\]/g, '<a href="#" class="card-link" data-card-id="$1">[[ $1 ]]</a>');
  return t;
}

export function parseMarkdown(input: string): string {
  const lines = (input ?? '').split('\n');
  let html = '';
  for (let raw of lines) {
    const line = raw.trim();
    // Headings
    const headingMatch = line.match(/^#{1,6}\s+(.*)$/);
    if (headingMatch) {
      const level = line.match(/^#{1,6}/)?.[0]?.length ?? 1;
      const content = headingMatch[1];
      html += `<h${level}>${inlineReplace(escapeHtml(content))}</h${level}>`;
      continue;
    }
    // Paragraphs (non-empty lines as <p>)
    if (line.length > 0) {
      html += `<p>${inlineReplace(escapeHtml(line))}</p>`;
    } else {
      // preserve blank lines as a newline in the HTML flow (no-op)
      html += '';
    }
  }
  return html;
}
