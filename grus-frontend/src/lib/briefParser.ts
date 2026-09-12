import { Severity } from "./types";

/**
 * The /brief endpoint returns markdown-ish text, not JSON (this changed
 * from the original contract). Shape, per the frontend brief doc:
 *
 *   **SUMMARY**
 *   one line
 *
 *   **RED FLAGS**
 *   [critical] TITLE
 *     detail with values [labs#301694]
 *     -> action
 *
 *   **CURRENT STATE**
 *   **HISTORY**
 *   **CRITICAL UNKNOWNS**
 *
 * This parser is deliberately forgiving: unknown headings still render
 * (as plain prose), missing [tag]s still produce a block, and every
 * [table#id] token anywhere in the text becomes a clickable citation.
 */

export type CitationToken = { type: "citation"; table: string; id: number; raw: string };
export type TextToken = { type: "text"; value: string };
export type LineToken = CitationToken | TextToken;

export interface BriefBlock {
  tag?: Severity;
  title: string;
  detailLines: string[];
  action?: string;
}

export interface BriefSection {
  heading: string; // "" for any preamble text before the first heading
  blocks: BriefBlock[];
}

export interface ParsedBrief {
  sections: BriefSection[];
}

const HEADING_RE = /^\*\*(.+?)\*\*\s*$/;
const TAG_RE = /^\[(critical|warning|info|unknown)\]\s*(.*)$/i;
const ACTION_RE = /^(->|→)\s*(.*)$/;

export function parseBrief(text: string): ParsedBrief {
  const lines = (text || "").split(/\r?\n/);
  const sections: BriefSection[] = [];
  let current: BriefSection | null = null;
  let currentBlock: BriefBlock | null = null;

  const pushBlock = () => {
    if (currentBlock && current) current.blocks.push(currentBlock);
    currentBlock = null;
  };
  const pushSection = () => {
    pushBlock();
    if (current) sections.push(current);
  };
  const ensureSection = () => {
    if (!current) current = { heading: "", blocks: [] };
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\t/g, "  ");
    const trimmed = line.trim();

    const headingMatch = trimmed.match(HEADING_RE);
    if (headingMatch) {
      pushSection();
      current = { heading: headingMatch[1].trim(), blocks: [] };
      continue;
    }

    ensureSection();

    if (trimmed === "") {
      pushBlock();
      continue;
    }

    const tagMatch = trimmed.match(TAG_RE);
    const actionMatch = trimmed.match(ACTION_RE);
    const isIndented = /^\s/.test(line);

    if (tagMatch) {
      pushBlock();
      currentBlock = {
        tag: tagMatch[1].toLowerCase() as Severity,
        title: tagMatch[2].trim(),
        detailLines: [],
      };
    } else if (actionMatch && currentBlock) {
      currentBlock.action = actionMatch[2].trim();
    } else if (currentBlock && isIndented) {
      currentBlock.detailLines.push(trimmed);
    } else {
      // Either a new untagged block, or plain prose. Start a fresh block
      // so paragraph-style sections (SUMMARY, CURRENT STATE) still get
      // something to render — each becomes a "title-only" block that the
      // renderer treats as a paragraph line.
      pushBlock();
      currentBlock = { title: trimmed, detailLines: [] };
    }
  }
  pushSection();

  return { sections: sections.filter((s) => s.heading || s.blocks.length > 0) };
}

const CITATION_RE = /\[([a-zA-Z_]+)#(\d+)\]/g;

/** Split a line of text into plain-text and citation tokens for rendering. */
export function tokenizeCitations(text: string): LineToken[] {
  const tokens: LineToken[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  CITATION_RE.lastIndex = 0;
  while ((match = CITATION_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ type: "text", value: text.slice(lastIndex, match.index) });
    }
    tokens.push({ type: "citation", table: match[1], id: Number(match[2]), raw: match[0] });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    tokens.push({ type: "text", value: text.slice(lastIndex) });
  }
  return tokens;
}

export function isRedFlagsHeading(h: string) {
  return /red flag/i.test(h);
}
export function isCriticalUnknownsHeading(h: string) {
  return /critical unknown/i.test(h);
}
export function isResolvedHeading(h: string) {
  return /resolved/i.test(h);
}
