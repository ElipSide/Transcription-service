import type { ReactNode } from "react";

/**
 * Tiny, dependency-free Markdown renderer for the subset the AI produces:
 * headings, bullet/numbered lists, paragraphs, **bold** and `code`.
 * Returns React nodes (no dangerouslySetInnerHTML) so it is XSS-safe.
 */

function renderInline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const regex = /\*\*(.+?)\*\*|`([^`]+)`/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = regex.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      nodes.push(<strong key={`${keyBase}-b${i}`}>{m[1]}</strong>);
    } else if (m[2] !== undefined) {
      nodes.push(<code key={`${keyBase}-c${i}`}>{m[2]}</code>);
    }
    last = regex.lastIndex;
    i++;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let listItems: ReactNode[] | null = null;
  let listType: "ul" | "ol" | null = null;
  let key = 0;

  const flushList = () => {
    if (listItems && listType) {
      const Tag = listType;
      blocks.push(<Tag key={`l${key++}`}>{listItems}</Tag>);
    }
    listItems = null;
    listType = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushList();
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flushList();
      const level = heading[1].length;
      const Tag = (`h${Math.min(level + 1, 5)}`) as "h2" | "h3" | "h4" | "h5";
      blocks.push(<Tag key={`h${key++}`}>{renderInline(heading[2], `h${key}`)}</Tag>);
      continue;
    }

    const bullet = /^[-*•·—]\s+(.*)$/.exec(line);
    if (bullet) {
      if (listType !== "ul") {
        flushList();
        listItems = [];
        listType = "ul";
      }
      listItems!.push(
        <li key={`li${key++}`}>{renderInline(bullet[1], `li${key}`)}</li>
      );
      continue;
    }

    const numbered = /^(\d+)[.)]\s+(.*)$/.exec(line);
    if (numbered) {
      if (listType !== "ol") {
        flushList();
        listItems = [];
        listType = "ol";
      }
      listItems!.push(
        <li key={`li${key++}`}>{renderInline(numbered[2], `li${key}`)}</li>
      );
      continue;
    }

    flushList();
    blocks.push(<p key={`p${key++}`}>{renderInline(line, `p${key}`)}</p>);
  }
  flushList();

  return <div className={className}>{blocks}</div>;
}
