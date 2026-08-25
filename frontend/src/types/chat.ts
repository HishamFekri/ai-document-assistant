export type User = {
  id: number;
  email?: string;
  name: string | null;
  picture: string | null;
  created_at?: string;
};


export type Document = {
  id: number;
  user_id?: number | null;

  filename: string;
  file_type: string | null;

  pages_count: number | null;

  processing_status:
    | "processing"
    | "ready"
    | "failed"
    | string;

  processing_stage:
    string | null;

  processing_progress:
    number;

  processing_error:
    string | null;

  created_at: string;
};


export type Chat = {
  id: number;
  user_id?: number | null;

  title: string | null;

  is_pinned: boolean;
  is_archived: boolean;

  created_at: string;

  documents: Document[];
};


export type ChatListItem = {
  id: number;

  title: string | null;

  is_pinned: boolean;
  is_archived: boolean;

  created_at: string;
};


export type Source = {
  source_id?: string;

  document_id?: number;
  chunk_id?: number;

  filename?: string;

  location?: string;
  content_type?: string;

  similarity?: number;

  snippet?: string;
  content?: string;

  asset_url?: string;
  asset_filename?: string;

  [key: string]:
    unknown;
};


export type Message = {
  id: number;
  chat_id: number;

  role:
    | "user"
    | "assistant";

  content: string;

  status: string;

  error:
    string | null;

  sources:
    Source[] | null;

  documents:
    Document[];

  created_at: string;
};