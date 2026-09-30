import { authFetchWithToken } from './auth';
import type { BackendTask } from '../types/pipeline';

export async function listTasks(sessionId?: string): Promise<BackendTask[]> {
  const url = sessionId 
    ? `/api/tasks?session_id=${encodeURIComponent(sessionId)}`
    : '/api/tasks';
  return authFetchWithToken<BackendTask[]>(url);
}

export async function listRunningTasks(): Promise<BackendTask[]> {
  return authFetchWithToken<BackendTask[]>('/api/tasks/running');
}

export async function getTask(taskId: string): Promise<BackendTask> {
  return authFetchWithToken<BackendTask>(`/api/tasks/${taskId}`);
}

export async function cancelTask(taskId: string): Promise<{ status: string; task_id: string }> {
  return authFetchWithToken<{ status: string; task_id: string }>(`/api/tasks/${taskId}`, {
    method: 'DELETE',
  });
}

export async function removeTask(taskId: string): Promise<{ status: string; task_id: string }> {
  return authFetchWithToken<{ status: string; task_id: string }>(`/api/tasks/${taskId}/remove`, {
    method: 'DELETE',
  });
}

export function getTaskStreamUrl(taskId: string): string {
  return `/api/tasks/${taskId}/stream`;
}
