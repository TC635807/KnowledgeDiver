export interface Card {
  id: string;
  title: string;
  content: string;
  parent_id?: string | null;
  metadata?: Record<string, string | number | string[]>;
  created_at?: string | Date;
  updated_at?: string | Date;
  links?: string[];
  backlinks?: string[];
  sources?: string[];
  confidence?: number;
  tags?: string[];
}
