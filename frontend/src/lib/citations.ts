// Compact display for source references the model writes inside an answer, such as
// "(Source: Report.pdf, Page 18, Clause 14.2)" or "[Page 37]". The answer text is
// unchanged; each reference is rendered as a short label with the full text kept
// available on hover, focus or tap.

export type CitationPart =
  | { kind: "text"; text: string }
  | { kind: "citation"; label: string; full: string };


// One level of nested parentheses covers filenames like "Report (final).pdf".
const BRACKETED = /\((?:[^()]|\([^()]*\))*\)|\[[^[\]]*\]/g;

const PAGE = /(?:\bpages?|\bpp?\.|صفح(?:ة|ات))\s*(\d+)(?:\s*(?:-|–|—|to|and|&)\s*(\d+))?/i;
const CLAUSE = /(?:\bclause|\bsection|\bart(?:icle|\.)|§)\s*(\d+(?:\.\d+)*)/i;
const FILE = /([^,;:]*?\.(?:pdf|docx?|xlsx?|pptx?|txt|csv|md))(?=[\s,;)\]]|$)/i;
const SOURCE_PREFIX = /^\s*(?:sources?|references?|ref\.?|المصدر)\s*:\s*/i;

const MAX_REFERENCE_LENGTH = 220;
const MAX_FILE_LABEL = 18;


function shortFileName(
  filename: string
) {
  const name =
    filename
      .replace(SOURCE_PREFIX, "")
      .replace(/\.[a-z0-9]+$/i, "")
      .trim();

  return name.length > MAX_FILE_LABEL
    ? `${name.slice(0, MAX_FILE_LABEL - 1).trimEnd()}…`
    : name;
}


export function pageLabel(
  text: string
): string | null {
  const page =
    PAGE.exec(text);

  if (!page) {
    return null;
  }

  return page[2] && page[2] !== page[1]
    ? `pp. ${page[1]}–${page[2]}`
    : `p. ${page[1]}`;
}


export function citationLabel(
  reference: string
): string | null {
  const inner =
    reference
      .slice(1, -1)
      .trim();

  if (
    !inner
    || inner.length > MAX_REFERENCE_LENGTH
  ) {
    return null;
  }

  const page =
    pageLabel(inner);

  if (page) {
    return page;
  }

  const file =
    FILE.exec(inner);

  if (file) {
    return shortFileName(file[1]);
  }

  const clause =
    CLAUSE.exec(inner);

  // A bare clause number only counts when the bracket holds little else.
  if (
    clause
    && inner.length <= 40
  ) {
    return `§ ${clause[1]}`;
  }

  return SOURCE_PREFIX.test(inner)
    ? "Source"
    : null;
}


export function splitCitations(
  text: string
): CitationPart[] {
  const parts: CitationPart[] = [];
  let last = 0;

  for (const match of text.matchAll(BRACKETED)) {
    const label =
      citationLabel(match[0]);

    if (!label) {
      continue;
    }

    const start =
      match.index ?? 0;

    // Drop the space before the reference; the label carries its own spacing.
    const before =
      text
        .slice(last, start)
        .replace(/[ \t]+$/, "");

    if (before) {
      parts.push({ kind: "text", text: before });
    }

    parts.push({
      kind: "citation",
      label,
      full: match[0]
        .slice(1, -1)
        .replace(SOURCE_PREFIX, "")
        .trim(),
    });

    last = start + match[0].length;
  }

  if (last === 0) {
    return [{ kind: "text", text }];
  }

  if (last < text.length) {
    parts.push({ kind: "text", text: text.slice(last) });
  }

  return parts;
}


type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

const SKIPPED_TAGS = new Set(["a", "code", "pre", "script", "style"]);


function skipped(
  node: HastNode
) {
  if (
    node.tagName
    && SKIPPED_TAGS.has(node.tagName)
  ) {
    return true;
  }

  const className =
    node.properties?.className;

  return Array.isArray(className)
    && className.some(
      (name) =>
        typeof name === "string"
        && (name.startsWith("math") || name.startsWith("katex"))
    );
}


function transform(
  node: HastNode
) {
  if (
    !node.children
    || skipped(node)
  ) {
    return;
  }

  node.children = node.children.flatMap((child) => {
    if (child.type !== "text" || !child.value) {
      transform(child);
      return [child];
    }

    return splitCitations(child.value).map((part): HastNode =>
      part.kind === "text"
        ? { type: "text", value: part.text }
        : {
            type: "element",
            tagName: "span",
            properties: {
              className: ["aura-cite"],
              dataCite: part.full,
            },
            children: [{ type: "text", value: part.label }],
          }
    );
  });
}


// Rehype plugin for react-markdown; links, code and math are left untouched.
export function rehypeCitations() {
  return (tree: HastNode) => {
    transform(tree);
  };
}
