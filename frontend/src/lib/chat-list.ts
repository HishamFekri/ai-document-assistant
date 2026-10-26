import { mergePageItems } from "@/lib/pagination";
import type { Chat, ChatListItem } from "@/types/chat";


export function toChatListItem(
  chat: Chat
): ChatListItem {
  return {
    id: chat.id,
    title: chat.title,
    is_pinned: chat.is_pinned,
    is_archived: chat.is_archived,
    created_at: chat.created_at,
    last_activity_at: chat.last_activity_at,
  };
}


// Adds or refreshes one chat in the sidebar list, keeping the backend's order
// (archived last, pinned first, then latest activity) and never duplicating an id.
export function upsertChatListItem(
  chats: ChatListItem[],
  chat: Chat | ChatListItem
): ChatListItem[] {
  const item =
    "documents" in chat
      ? toChatListItem(chat)
      : chat;

  return mergePageItems(chats, [item], "chats");
}


export function retitleChatListItem(
  chats: ChatListItem[],
  chatId: number,
  title: string
): ChatListItem[] {
  return chats.map((item) =>
    item.id === chatId
      ? { ...item, title }
      : item
  );
}


// Applies a pin or archive change from the server and re-sorts, so the chat
// lands where GET /chats would place it (Recent by latest activity, pinned and
// archived by creation time). Activity itself is never changed here.
export function withUpdatedFlags(
  chats: ChatListItem[],
  chatId: number,
  updated: Pick<ChatListItem, "is_pinned" | "is_archived"> & { last_activity_at?: string }
): ChatListItem[] {
  const item =
    chats.find(
      (chat) => chat.id === chatId
    );

  if (!item) {
    return chats;
  }

  return upsertChatListItem(chats, {
    ...item,
    is_pinned: updated.is_pinned,
    is_archived: updated.is_archived,
    last_activity_at: updated.last_activity_at ?? item.last_activity_at,
  });
}
