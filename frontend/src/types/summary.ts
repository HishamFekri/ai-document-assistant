export type SummaryBlockType =
  | "text"
  | "image"
  | "table"
  | "equation";


export type SummaryBlock = {
  type: SummaryBlockType;

  title: string | null;

  content: string | null;

  asset_id: number | null;

  caption: string | null;

  location: string | null;
};


export type SummaryContent = {
  title: string;

  sections: SummaryBlock[];
};


export type DocumentSummary = {
  id: number;

  chat_id: number | null;

  document_id: number;

  mode:
    | "summary"
    | "transcription";

  version: number;

  status: string;

  content: SummaryContent | null;

  is_selected: boolean;

  error: string | null;

  created_at: string;
};