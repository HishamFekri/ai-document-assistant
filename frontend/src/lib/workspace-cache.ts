import type { ChatListItem, User } from "@/types/chat";

// The chat workspace remounts for every conversation. Keeping the signed-in user
// and the chat list across those remounts (memory only, never persisted) lets the
// sidebar stay in place while they revalidate, instead of flashing a full-screen
// loader. Cleared on sign-out and when the session expires.
export type WorkspaceSnapshot = {
  user: User;
  chats: ChatListItem[];
  chatCursor: string | null;
};

export const workspaceCache: { current: WorkspaceSnapshot | null } = { current: null };

export function clearWorkspaceCache() {
  workspaceCache.current = null;
}
