import { authFetchWithToken } from './auth';

export interface RawSource {
  url: string;
  title: string;
  content: string;
  fetched_at: string;
}

export async function fetchCardRaw(
  cardId: string,
  sessionId: string,
): Promise<RawSource[]> {
  const params = new URLSearchParams({ session_id: sessionId });
  const data = await authFetchWithToken<{ sources: RawSource[] }>(
    `/api/cards/${cardId}/raw?${params}`,
  );
  return data.sources || [];
}
