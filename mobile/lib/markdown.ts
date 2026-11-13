// A deliberately small Markdown parser for the privacy notice: `#` / `##` headings,
// paragraphs, `-` or `*` bullet lists and **bold** inline. Anything else is plain text.
// Pure functions only, so it can be unit-tested without rendering.

export type InlineSpan = { text: string; bold: boolean };

export type MarkdownBlock =
  | { type: "h1" | "h2"; spans: InlineSpan[] }
  | { type: "p"; spans: InlineSpan[] }
  | { type: "li"; spans: InlineSpan[] };

/** Splits a line into plain and **bold** spans. An unclosed ** stays literal text. */
export function parseInline(text: string): InlineSpan[] {
  const spans: InlineSpan[] = [];
  // Odd-indexed parts are the captured **...** groups.
  text.split(/\*\*([^*]+)\*\*/).forEach((part, index) => {
    if (part === "") return;
    spans.push({ text: part, bold: index % 2 === 1 });
  });
  return spans;
}

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^[-*]\s+(.*)$/;

/** Parses the notice into blocks. Consecutive text lines form one paragraph. */
export function parseMarkdown(source: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    blocks.push({ type: "p", spans: parseInline(paragraph.join(" ")) });
    paragraph = [];
  };

  for (const rawLine of source.replace(/\r\n?/g, "\n").split("\n")) {
    const line = rawLine.trim();
    if (line === "") {
      flushParagraph();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flushParagraph();
      blocks.push({ type: heading[1].length === 1 ? "h1" : "h2", spans: parseInline(heading[2]) });
      continue;
    }
    const bullet = BULLET.exec(line);
    if (bullet) {
      flushParagraph();
      blocks.push({ type: "li", spans: parseInline(bullet[1]) });
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  return blocks;
}
