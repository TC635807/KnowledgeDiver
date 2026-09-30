import { ApiConfig } from '../types/config';

export function validateApiUrl(url: string): boolean {
  if (!url) return false;
  try {
    new URL(url);
    return true;
  } catch (err) {
    console.warn('URL validation failed:', err);
    return false;
  }
}

export function validateApiKey(key: string): boolean {
  return typeof key === 'string' && key.trim().length > 0;
}

export function validateModel(model: string): boolean {
  return typeof model === 'string' && model.trim().length > 0;
}

export function validateTemperature(temp: number): boolean {
  if (typeof temp !== 'number' || Number.isNaN(temp)) return false;
  return temp >= 0 && temp <= 2;
}

export function validateMaxTokens(tokens: number): boolean {
  if (typeof tokens !== 'number' || Number.isNaN(tokens)) return false;
  return tokens > 0;
}


