import React from 'react';

export function sanitizeHref(rawUrl) {
  if (!rawUrl || typeof rawUrl !== 'string') return null;
  const trimmed = rawUrl.trim();
  if (
    trimmed.startsWith('https://') ||
    trimmed.startsWith('http://') ||
    trimmed.startsWith('/')
  ) {
    return trimmed;
  }
  return null;
}

export function renderInline(text, keyPrefix = 'in') {
  if (!text) return null;
  const tokens = [];
  const pattern = /(\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*|`([^`]+)`|\*([^*]+)\*)/g;
  let lastIdx = 0;
  let match;
  let count = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIdx) {
      tokens.push(text.slice(lastIdx, match.index));
    }

    if (match[2] !== undefined && match[3] !== undefined) {
      const safeUrl = sanitizeHref(match[3]);
      if (safeUrl) {
        tokens.push(
          <a
            key={`${keyPrefix}-${count++}`}
            href={safeUrl}
            target={safeUrl.startsWith('/') ? undefined : '_blank'}
            rel={safeUrl.startsWith('/') ? undefined : 'noopener noreferrer'}
            className="text-cyan-400 hover:text-cyan-300 underline decoration-cyan-500/40 underline-offset-2"
          >
            {match[2]}
          </a>
        );
      } else {
        tokens.push(`${match[2]} (${match[3]})`);
      }
    } else if (match[4] !== undefined) {
      tokens.push(
        <strong key={`${keyPrefix}-${count++}`} className="font-semibold text-white">
          {match[4]}
        </strong>
      );
    } else if (match[5] !== undefined) {
      tokens.push(
        <code
          key={`${keyPrefix}-${count++}`}
          className="px-1.5 py-0.5 mx-0.5 rounded bg-slate-900/90 border border-slate-700/60 text-cyan-300 font-mono text-[12px]"
        >
          {match[5]}
        </code>
      );
    } else if (match[6] !== undefined) {
      tokens.push(
        <em key={`${keyPrefix}-${count++}`} className="italic text-slate-300">
          {match[6]}
        </em>
      );
    }

    lastIdx = match.index + match[0].length;
  }

  if (lastIdx < text.length) {
    tokens.push(text.slice(lastIdx));
  }

  return tokens;
}

function renderTextBlock(content, blockKey) {
  const lines = content.split('\n');
  const elements = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      elements.push(<div key={`${blockKey}-sp-${i}`} className="h-1.5" />);
      i++;
      continue;
    }

    if (/^[-*_]{3,}$/.test(trimmed)) {
      elements.push(<hr key={`${blockKey}-hr-${i}`} className="my-2.5 border-slate-700/60" />);
      i++;
      continue;
    }

    const headingMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const cls =
        level === 1
          ? 'text-base font-bold text-white mt-2 mb-1'
          : level === 2
          ? 'text-sm font-bold text-slate-100 mt-2 mb-1'
          : level === 3
          ? 'text-sm font-bold text-cyan-300 mt-1.5 mb-0.5'
          : 'text-xs font-bold uppercase tracking-wide text-cyan-200 mt-1.5 mb-0.5';
      elements.push(
        <div key={`${blockKey}-h-${i}`} className={cls}>
          {renderInline(headingMatch[2], `${blockKey}-h${i}`)}
        </div>
      );
      i++;
      continue;
    }

    if (trimmed.startsWith('> ')) {
      const quoteLines = [];
      while (i < lines.length && lines[i].trim().startsWith('> ')) {
        quoteLines.push(lines[i].trim().slice(2));
        i++;
      }
      elements.push(
        <blockquote
          key={`${blockKey}-q-${i}`}
          className="my-2 pl-3 py-1 border-l-2 border-cyan-500/60 bg-slate-900/30 text-slate-300 text-xs rounded-r"
        >
          {quoteLines.map((q, qi) => (
            <div key={qi}>{renderInline(q, `${blockKey}-q${i}-${qi}`)}</div>
          ))}
        </blockquote>
      );
      continue;
    }

    if (
      trimmed.startsWith('|') &&
      trimmed.endsWith('|') &&
      i + 1 < lines.length &&
      lines[i + 1].trim().startsWith('|')
    ) {
      const tableLines = [];
      const startIdx = i;
      while (
        i < lines.length &&
        lines[i].trim().startsWith('|') &&
        lines[i].trim().endsWith('|')
      ) {
        tableLines.push(lines[i].trim());
        i++;
      }
      const rows = tableLines
        .filter((rowStr) => !/^\|[\s:|-]+\|$/.test(rowStr))
        .map((rowStr) => rowStr.slice(1, -1).split('|').map((c) => c.trim()));
      if (rows.length > 0) {
        const [headerRow, ...bodyRows] = rows;
        elements.push(
          <div
            key={`${blockKey}-tbl-${startIdx}`}
            className="my-2 overflow-x-auto rounded-lg border border-slate-700/60"
          >
            <table className="w-full text-xs text-left border-collapse">
              <thead className="bg-slate-900/80 text-cyan-300">
                <tr>
                  {headerRow.map((cell, ci) => (
                    <th key={ci} className="px-2.5 py-1.5 border-b border-slate-700/60 font-semibold">
                      {renderInline(cell, `${blockKey}-tbl${startIdx}-h-${ci}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {bodyRows.map((row, ri) => (
                  <tr key={ri} className="bg-slate-900/30">
                    {row.map((cell, ci) => (
                      <td key={ci} className="px-2.5 py-1.5 text-slate-200">
                        {renderInline(cell, `${blockKey}-tbl${startIdx}-r-${ri}-${ci}`)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
        continue;
      }
    }

    if (/^[-*•]\s+/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^[-*•]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*•]\s+/, ''));
        i++;
      }
      elements.push(
        <ul key={`${blockKey}-ul-${i}`} className="my-1.5 pl-5 list-disc space-y-1 text-slate-200">
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item, `${blockKey}-ul${i}-${idx}`)}</li>
          ))}
        </ul>
      );
      continue;
    }

    if (/^\d+[.)]\s+/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^\d+[.)]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+[.)]\s+/, ''));
        i++;
      }
      elements.push(
        <ol key={`${blockKey}-ol-${i}`} className="my-1.5 pl-5 list-decimal space-y-1 text-slate-200">
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item, `${blockKey}-ol${i}-${idx}`)}</li>
          ))}
        </ol>
      );
      continue;
    }

    elements.push(
      <p key={`${blockKey}-p-${i}`} className="my-0.5 leading-relaxed break-words whitespace-pre-wrap">
        {renderInline(line, `${blockKey}-p${i}`)}
      </p>
    );
    i++;
  }

  return elements;
}

export default function MarkdownContent({ text }) {
  if (!text) return null;
  const parts = [];
  const codeBlockRegex = /```(\w*)\n?([\s\S]*?)```/g;
  let lastIndex = 0;
  let match;

  while ((match = codeBlockRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: 'text', content: text.slice(lastIndex, match.index) });
    }
    parts.push({ type: 'code', language: match[1], content: match[2].trim() });
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push({ type: 'text', content: text.slice(lastIndex) });
  }

  return (
    <div className="space-y-1 break-words">
      {parts.map((part, idx) =>
        part.type === 'code' ? (
          <pre
            key={idx}
            className="my-2 p-3 bg-slate-900/90 rounded-lg border border-slate-700/60 overflow-x-auto text-xs"
          >
            {part.language && (
              <div className="text-[10px] text-slate-500 font-mono mb-1">{part.language}</div>
            )}
            <code className="text-slate-300 font-mono">{part.content}</code>
          </pre>
        ) : (
          <div key={idx}>{renderTextBlock(part.content, `blk-${idx}`)}</div>
        )
      )}
    </div>
  );
}
