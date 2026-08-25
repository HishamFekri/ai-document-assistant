"use client";

import {
  useEffect,
  useState,
} from "react";

import type {
  ReactNode,
} from "react";

import {
  Loader2,
} from "lucide-react";

import {
  SummaryBlock as SummaryBlockType,
} from "@/types/summary";


type Props = {
  block: SummaryBlockType;
  documentId: number;
  token: string;
};


type ParsedTable = {
  headers: string[];
  rows: string[][];
};


const API_URL =
  process.env.NEXT_PUBLIC_API_URL
  || "http://localhost:8000";


function decodeHtmlEntities(
  value: string
) {
  const named: Record<string, string> = {
    amp: "&",
    lt: "<",
    gt: ">",
    quot: "\"",
    apos: "'",
    nbsp: " ",
  };

  return value
    .replace(
      /&#(\d+);/g,
      (_match, code) => {
        const number = Number(code);

        return Number.isFinite(number)
          ? String.fromCodePoint(number)
          : "";
      }
    )
    .replace(
      /&#x([0-9a-f]+);/gi,
      (_match, code) => {
        const number = parseInt(code, 16);

        return Number.isFinite(number)
          ? String.fromCodePoint(number)
          : "";
      }
    )
    .replace(
      /&([a-z]+);/gi,
      (match, entity) =>
        named[entity.toLowerCase()]
        ?? match
    );
}


function cleanInlineMarkdown(
  value: string
) {
  return value
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/__(.*?)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .trim();
}


function htmlToPlainText(
  value: string
) {
  let cleaned = value;

  cleaned = cleaned.replace(
    /<img\b[^>]*\balt\s*=\s*["']([^"']*)["'][^>]*>/gi,
    "\n$1\n"
  );

  cleaned = cleaned.replace(
    /<br\s*\/?>/gi,
    "\n"
  );

  cleaned = cleaned.replace(
    /<\/(?:p|div|section|article|header|footer|h[1-6]|li|tr)>/gi,
    "\n"
  );

  cleaned = cleaned.replace(
    /<li\b[^>]*>/gi,
    "• "
  );

  cleaned = cleaned.replace(
    /<[^>]+>/g,
    ""
  );

  return decodeHtmlEntities(cleaned);
}


function cleanDisplayText(
  value: string | null
) {
  if (!value) {
    return "";
  }

  const cleaned =
    htmlToPlainText(value)
      .replace(/\r/g, "")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n[ \t]+/g, "\n")
      .replace(/[ \t]{2,}/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();

  const lines = cleaned
    .split("\n")
    .map((line) =>
      cleanInlineMarkdown(line.trim())
    )
    .filter(Boolean);

  const deduplicated: string[] = [];

  for (const line of lines) {
    if (
      deduplicated[
        deduplicated.length - 1
      ] === line
    ) {
      continue;
    }

    deduplicated.push(line);
  }

  return deduplicated.join("\n");
}


function splitTableCells(
  line: string
) {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((cell) =>
      cleanDisplayText(cell)
    );
}


function isMarkdownSeparator(
  line: string
) {
  const cells = splitTableCells(line);

  return (
    cells.length > 0
    && cells.every((cell) =>
      /^:?-{3,}:?$/.test(
        cell.replace(/\s/g, "")
      )
    )
  );
}


function normalizeRows(
  rows: string[][]
) {
  const columnCount = rows.reduce(
    (max, row) =>
      Math.max(max, row.length),
    0
  );

  return rows.map((row) => [
    ...row,
    ...Array(
      Math.max(
        0,
        columnCount - row.length
      )
    ).fill(""),
  ]);
}


function parseHtmlTable(
  content: string
): ParsedTable | null {
  if (!/<tr\b/i.test(content)) {
    return null;
  }

  const rowMatches = [
    ...content.matchAll(
      /<tr\b[^>]*>([\s\S]*?)<\/tr>/gi
    ),
  ];

  const rows: string[][] = [];
  let hasHeaderCells = false;

  for (const rowMatch of rowMatches) {
    const rowHtml = rowMatch[1];

    const cellMatches = [
      ...rowHtml.matchAll(
        /<(th|td)\b[^>]*>([\s\S]*?)<\/\1>/gi
      ),
    ];

    const row = cellMatches.map(
      (match) => {
        if (
          match[1].toLowerCase()
          === "th"
        ) {
          hasHeaderCells = true;
        }

        return cleanDisplayText(
          match[2]
        );
      }
    );

    if (row.some((cell) => cell)) {
      rows.push(row);
    }
  }

  if (!rows.length) {
    return null;
  }

  const normalized = normalizeRows(rows);

  if (
    hasHeaderCells
    && normalized.length > 1
  ) {
    return {
      headers: normalized[0],
      rows: normalized.slice(1),
    };
  }

  return {
    headers: [],
    rows: normalized,
  };
}


function parseMarkdownTable(
  content: string
): ParsedTable | null {
  const lines = content
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  if (lines.length < 2) {
    return null;
  }

  const tableLines = lines.filter(
    (line) => line.includes("|")
  );

  if (tableLines.length < 2) {
    return null;
  }

  const separatorIndex =
    tableLines.findIndex(
      isMarkdownSeparator
    );

  if (separatorIndex === 1) {
    const headers =
      splitTableCells(tableLines[0]);

    const rows = tableLines
      .slice(2)
      .map(splitTableCells)
      .filter((row) => row.some(Boolean));

    return {
      headers,
      rows: normalizeRows(rows),
    };
  }

  const rows = tableLines
    .filter(
      (line) =>
        !isMarkdownSeparator(line)
    )
    .map(splitTableCells)
    .filter((row) => row.some(Boolean));

  if (!rows.length) {
    return null;
  }

  return {
    headers: [],
    rows: normalizeRows(rows),
  };
}


function parseTable(
  content: string
) {
  return (
    parseHtmlTable(content)
    ?? parseMarkdownTable(content)
  );
}


function CleanTable({
  table,
}: {
  table: ParsedTable;
}) {
  const allRows = [
    ...(table.headers.length
      ? [table.headers]
      : []),
    ...table.rows,
  ];

  const columnCount = allRows.reduce(
    (max, row) =>
      Math.max(max, row.length),
    0
  );

  if (columnCount === 0) {
    return null;
  }

  return (
    <div
      className="
        overflow-x-auto
        rounded-lg
        border
        border-[var(--border)]
      "
    >
      <table
        className="
          w-full
          min-w-[520px]
          border-collapse
          text-sm
        "
      >
        {table.headers.length > 0 && (
          <thead>
            <tr
              className="
                bg-[var(--surface)]
              "
            >
              {Array.from({
                length: columnCount,
              }).map((_, index) => (
                <th
                  key={index}
                  dir="auto"
                  className="
                    border-b
                    border-r
                    border-[var(--border)]
                    px-3
                    py-2.5
                    text-start
                    font-semibold
                    text-[var(--text-primary)]
                    last:border-r-0
                  "
                >
                  {table.headers[index] ?? ""}
                </th>
              ))}
            </tr>
          </thead>
        )}

        <tbody>
          {table.rows.map(
            (row, rowIndex) => (
              <tr
                key={rowIndex}
                className="
                  border-b
                  border-[var(--border)]
                  last:border-b-0
                "
              >
                {Array.from({
                  length: columnCount,
                }).map(
                  (_, cellIndex) => (
                    <td
                      key={cellIndex}
                      dir="auto"
                      className="
                        border-r
                        border-[var(--border)]
                        px-3
                        py-2.5
                        align-top
                        leading-6
                        text-[var(--text-primary)]
                        last:border-r-0
                      "
                    >
                      {row[cellIndex] ?? ""}
                    </td>
                  )
                )}
              </tr>
            )
          )}
        </tbody>
      </table>
    </div>
  );
}


function renderTextContent(
  content: string
): ReactNode[] {
  const rawLines =
    htmlToPlainText(content)
      .replace(/\r/g, "")
      .split("\n");

  const nodes: ReactNode[] = [];
  let index = 0;

  while (index < rawLines.length) {
    const rawLine =
      rawLines[index].trim();

    if (!rawLine) {
      index += 1;
      continue;
    }

    if (rawLine.includes("|")) {
      const tableLines: string[] = [];
      let tableIndex = index;

      while (
        tableIndex < rawLines.length
        && rawLines[tableIndex].includes("|")
      ) {
        tableLines.push(
          rawLines[tableIndex]
        );

        tableIndex += 1;
      }

      const parsed =
        parseMarkdownTable(
          tableLines.join("\n")
        );

      if (parsed) {
        nodes.push(
          <CleanTable
            key={`table-${index}`}
            table={parsed}
          />
        );

        index = tableIndex;
        continue;
      }
    }

    const headingMatch =
      rawLine.match(
        /^(#{1,6})\s+(.+)$/
      );

    if (headingMatch) {
      nodes.push(
        <h4
          key={`heading-${index}`}
          dir="auto"
          className="
            mt-5
            text-[16px]
            font-semibold
            leading-7
            text-[var(--text-primary)]
            first:mt-0
          "
        >
          {cleanInlineMarkdown(
            headingMatch[2]
          )}
        </h4>
      );

      index += 1;
      continue;
    }

    const bulletMatch =
      rawLine.match(
        /^[-*•]\s+(.+)$/
      );

    if (bulletMatch) {
      const items: string[] = [];
      let listIndex = index;

      while (
        listIndex < rawLines.length
      ) {
        const match =
          rawLines[listIndex]
            .trim()
            .match(
              /^[-*•]\s+(.+)$/
            );

        if (!match) {
          break;
        }

        items.push(
          cleanInlineMarkdown(
            match[1]
          )
        );

        listIndex += 1;
      }

      nodes.push(
        <ul
          key={`list-${index}`}
          className="
            list-disc
            space-y-1.5
            ps-5
            text-[15px]
            leading-7
            text-[var(--text-primary)]
          "
        >
          {items.map(
            (item, itemIndex) => (
              <li
                key={itemIndex}
                dir="auto"
              >
                {item}
              </li>
            )
          )}
        </ul>
      );

      index = listIndex;
      continue;
    }

    const numberedMatch =
      rawLine.match(
        /^\d+[.)]\s+(.+)$/
      );

    if (numberedMatch) {
      const items: string[] = [];
      let listIndex = index;

      while (
        listIndex < rawLines.length
      ) {
        const match =
          rawLines[listIndex]
            .trim()
            .match(
              /^\d+[.)]\s+(.+)$/
            );

        if (!match) {
          break;
        }

        items.push(
          cleanInlineMarkdown(
            match[1]
          )
        );

        listIndex += 1;
      }

      nodes.push(
        <ol
          key={`ordered-${index}`}
          className="
            list-decimal
            space-y-1.5
            ps-5
            text-[15px]
            leading-7
            text-[var(--text-primary)]
          "
        >
          {items.map(
            (item, itemIndex) => (
              <li
                key={itemIndex}
                dir="auto"
              >
                {item}
              </li>
            )
          )}
        </ol>
      );

      index = listIndex;
      continue;
    }

    const paragraphLines = [
      cleanInlineMarkdown(rawLine),
    ];

    let paragraphIndex = index + 1;

    while (
      paragraphIndex < rawLines.length
    ) {
      const next =
        rawLines[paragraphIndex].trim();

      if (
        !next
        || next.includes("|")
        || /^(#{1,6})\s+/.test(next)
        || /^[-*•]\s+/.test(next)
        || /^\d+[.)]\s+/.test(next)
      ) {
        break;
      }

      paragraphLines.push(
        cleanInlineMarkdown(next)
      );

      paragraphIndex += 1;
    }

    nodes.push(
      <p
        key={`paragraph-${index}`}
        dir="auto"
        className="
          whitespace-pre-wrap
          text-[15px]
          leading-8
          text-[var(--text-primary)]
        "
      >
        {paragraphLines
          .filter(Boolean)
          .join(" ")}
      </p>
    );

    index = paragraphIndex;
  }

  return nodes;
}


function TextBlock({
  block,
}: {
  block: SummaryBlockType;
}) {
  return (
    <section>
      {block.title && (
        <h3
          dir="auto"
          className="
            mb-3
            text-[17px]
            font-semibold
            leading-7
            text-[var(--text-primary)]
          "
        >
          {cleanDisplayText(
            block.title
          )}
        </h3>
      )}

      {block.content && (
        <div className="space-y-4">
          {renderTextContent(
            block.content
          )}
        </div>
      )}
    </section>
  );
}


function AuthenticatedImage({
  documentId,
  assetId,
  token,
  alt,
}: {
  documentId: number;
  assetId: number;
  token: string;
  alt: string;
}) {
  const [
    imageUrl,
    setImageUrl,
  ] = useState<string | null>(
    null
  );

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    error,
    setError,
  ] = useState<string | null>(
    null
  );


  useEffect(() => {
    let objectUrl: string | null =
      null;

    let cancelled = false;


    async function loadImage() {
      try {
        setLoading(true);
        setError(null);

        const response =
          await fetch(
            `${API_URL}/documents/${documentId}/assets/${assetId}/file`,
            {
              method: "GET",
              credentials: "include",
              cache: "no-store",
            }
          );

        if (!response.ok) {
          throw new Error(
            "Could not load image"
          );
        }

        const blob =
          await response.blob();

        if (cancelled) {
          return;
        }

        objectUrl =
          URL.createObjectURL(blob);

        setImageUrl(objectUrl);

      } catch (error) {
        if (cancelled) {
          return;
        }

        console.error(
          "[SUMMARY IMAGE ERROR]",
          error
        );

        setError(
          "Could not load this image"
        );

      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }


    void loadImage();


    return () => {
      cancelled = true;

      if (objectUrl) {
        URL.revokeObjectURL(
          objectUrl
        );
      }
    };
  }, [
    documentId,
    assetId,
    token,
  ]);


  if (loading) {
    return (
      <div
        className="
          flex
          min-h-44
          items-center
          justify-center
          text-[var(--text-muted)]
        "
      >
        <Loader2
          size={19}
          className="animate-spin"
        />
      </div>
    );
  }


  if (error || !imageUrl) {
    return (
      <p
        className="
          py-6
          text-center
          text-xs
          text-[var(--text-muted)]
        "
      >
        {error || "Image unavailable"}
      </p>
    );
  }


  return (
    <img
      src={imageUrl}
      alt={alt}
      className="
        block
        h-auto
        max-h-[560px]
        w-full
        rounded-xl
        object-contain
      "
    />
  );
}


function ImageBlock({
  block,
  documentId,
  token,
}: Props) {
  if (block.asset_id === null) {
    return null;
  }

  const title =
    cleanDisplayText(
      block.title
    );

  const caption =
    cleanDisplayText(
      block.caption
    );

  return (
    <section>
      {title && (
        <h3
          dir="auto"
          className="
            mb-3
            text-[16px]
            font-semibold
            leading-7
            text-[var(--text-primary)]
          "
        >
          {title}
        </h3>
      )}

      <AuthenticatedImage
        documentId={documentId}
        assetId={block.asset_id}
        token={token}
        alt={
          caption
          || title
          || "Document image"
        }
      />

      {caption && (
        <p
          dir="auto"
          className="
            mt-2
            text-[12px]
            leading-5
            text-[var(--text-muted)]
          "
        >
          {caption}
        </p>
      )}
    </section>
  );
}


function TableBlock({
  block,
}: {
  block: SummaryBlockType;
}) {
  const title =
    cleanDisplayText(
      block.title
    );

  const caption =
    cleanDisplayText(
      block.caption
    );

  const table =
    block.content
      ? parseTable(block.content)
      : null;

  const fallback =
    block.content
      ? cleanDisplayText(
          block.content
        )
      : "";

  return (
    <section>
      {title && (
        <h3
          dir="auto"
          className="
            mb-3
            text-[16px]
            font-semibold
            leading-7
            text-[var(--text-primary)]
          "
        >
          {title}
        </h3>
      )}

      {caption && (
        <p
          dir="auto"
          className="
            mb-3
            text-[13px]
            leading-6
            text-[var(--text-secondary)]
          "
        >
          {caption}
        </p>
      )}

      {table ? (
        <CleanTable table={table} />
      ) : (
        fallback && (
          <div
            dir="auto"
            className="
              whitespace-pre-wrap
              text-[14px]
              leading-7
              text-[var(--text-primary)]
            "
          >
            {fallback}
          </div>
        )
      )}
    </section>
  );
}


function EquationBlock({
  block,
}: {
  block: SummaryBlockType;
}) {
  const title =
    cleanDisplayText(
      block.title
    );

  const caption =
    cleanDisplayText(
      block.caption
    );

  const content =
    cleanDisplayText(
      block.content
    );

  return (
    <section>
      {title && (
        <h3
          dir="auto"
          className="
            mb-3
            text-[16px]
            font-semibold
            leading-7
            text-[var(--text-primary)]
          "
        >
          {title}
        </h3>
      )}

      {content && (
        <div
          dir="ltr"
          className="
            overflow-x-auto
            whitespace-pre-wrap
            font-mono
            text-[14px]
            leading-7
            text-[var(--text-primary)]
          "
        >
          {content}
        </div>
      )}

      {caption && (
        <p
          dir="auto"
          className="
            mt-2
            text-[12px]
            leading-5
            text-[var(--text-muted)]
          "
        >
          {caption}
        </p>
      )}
    </section>
  );
}


export default function SummaryBlock({
  block,
  documentId,
  token,
}: Props) {
  if (block.type === "text") {
    return (
      <TextBlock block={block} />
    );
  }

  if (block.type === "image") {
    return (
      <ImageBlock
        block={block}
        documentId={documentId}
        token={token}
      />
    );
  }

  if (block.type === "table") {
    return (
      <TableBlock block={block} />
    );
  }

  if (block.type === "equation") {
    return (
      <EquationBlock block={block} />
    );
  }

  return null;
}