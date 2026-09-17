export type Page<T> = { items: T[]; nextCursor: string | null };

export async function readPage<T>(response: Response): Promise<Page<T>> {
  const items: unknown = await response.json();
  if (!Array.isArray(items)) throw new Error("Invalid page response");
  return { items: items as T[], nextCursor: response.headers.get("X-Next-Cursor") };
}

type OrderedItem = {
  id: number;
  created_at: string;
  is_archived?: boolean;
  is_pinned?: boolean;
};

export function mergePageItems<T extends OrderedItem>(
  current: T[], incoming: T[], order: "messages" | "chats" = "messages",
): T[] {
  const byId = new Map(current.map((item) => [item.id, item]));
  for (const item of incoming) byId.set(item.id, item);
  return [...byId.values()].sort((a, b) => {
    if (order === "chats") {
      const group = Number(Boolean(a.is_archived)) - Number(Boolean(b.is_archived))
        || Number(Boolean(b.is_pinned)) - Number(Boolean(a.is_pinned));
      if (group) return group;
    }
    const chronological = a.created_at.localeCompare(b.created_at) || a.id - b.id;
    return order === "chats" ? -chronological : chronological;
  });
}
