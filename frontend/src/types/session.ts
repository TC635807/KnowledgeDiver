export interface Session {
  id: string;
  name: string;
  created_at: string | Date;
  updated_at: string | Date;
  card_count: number;
}

export interface SessionCreate {
  name: string;
}

export interface SessionUpdate {
  name: string;
}