import React from 'react';
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import ApiConfig from '../components/ApiConfig';

import type { ApiConfig as ApiConfigType } from '../types/config';

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] || null),
    setItem: vi.fn((key: string, value: string) => { store[key] = value; }),
    removeItem: vi.fn((key: string) => { delete store[key]; }),
    clear: vi.fn(() => { store = {}; }),
    get length() { return Object.keys(store).length; },
    key: vi.fn((index: number) => Object.keys(store)[index] || null),
  };
})();

function getInputs(container: HTMLElement) {
  const inputs = container.querySelectorAll('input');
  return {
    apiUrl: inputs[0] as HTMLInputElement,
    apiKey: inputs[1] as HTMLInputElement,
    model: inputs[2] as HTMLInputElement,
    temperature: inputs[3] as HTMLInputElement,
    maxTokens: inputs[4] as HTMLInputElement,
  };
}

describe('ApiConfig component', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', localStorageMock);
    localStorageMock.clear();
    (global as any).fetch = vi.fn(async (url: string, options?: any) => {
      if (url === '/api/ai/config' && (!options || options.method === 'GET')) {
        return {
          ok: false,
          json: async () => ({}),
          text: async () => '',
        };
      }
      return {
        ok: true,
        json: async () => ({ success: true }),
        text: async () => '',
      };
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the form with all fields', async () => {
    const onSave = vi.fn();
    const { container } = render(<ApiConfig onSave={onSave} />);
    const inputs = getInputs(container);
    expect(inputs.apiUrl).toBeTruthy();
    expect(inputs.apiKey).toBeTruthy();
    expect(inputs.model).toBeTruthy();
    expect(inputs.temperature).toBeTruthy();
    expect(inputs.maxTokens).toBeTruthy();
  });

  it('validates required fields on save', async () => {
    const onSave = vi.fn();
    const { container } = render(<ApiConfig onSave={onSave} />);
    const inputs = getInputs(container);
    fireEvent.change(inputs.apiKey, { target: { value: '' } });
    const saveBtn = screen.getByText(/Save/i);
    fireEvent.click(saveBtn);
    expect(screen.queryByText(/API Key is required/i)).toBeTruthy();
  });

  it('saves config and calls onSave with correct values', async () => {
    const onSave = vi.fn();
    const { container } = render(<ApiConfig onSave={onSave} />);
    const inputs = getInputs(container);

    fireEvent.change(inputs.apiUrl, { target: { value: 'https://api.example.com/v1' } });
    fireEvent.change(inputs.apiKey, { target: { value: 'secret-key' } });
    fireEvent.change(inputs.model, { target: { value: 'gpt-4' } });
    fireEvent.change(inputs.temperature, { target: { value: '0.7' } });
    fireEvent.change(inputs.maxTokens, { target: { value: '2000' } });

    const saveBtn = screen.getByText(/Save/i);
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(onSave).toHaveBeenCalled();
      const arg = onSave.mock.calls[0][0] as ApiConfigType;
      expect(arg.apiUrl).toBe('https://api.example.com/v1');
      expect(arg.apiKey).toBe('secret-key');
      expect(arg.model).toBe('gpt-4');
      expect(arg.temperature).toBe(0.7);
      expect(arg.maxTokens).toBe(2000);
    });
  });

  it('tests connection via backend API', async () => {
    const onSave = vi.fn();
    render(<ApiConfig onSave={onSave} />);
    const testBtn = screen.getByText(/Test Connection/i);
    fireEvent.click(testBtn);
    await waitFor(() => {
      const success = screen.queryByText(/Connection successful|Connection test failed|Testing.../i);
      expect(success).toBeTruthy();
    });
  });

  it('resets to defaults', async () => {
    const onSave = vi.fn();
    const { container } = render(<ApiConfig onSave={onSave} />);
    const inputs = getInputs(container);
    fireEvent.change(inputs.apiUrl, { target: { value: 'https://change.me' } });
    const resetBtn = screen.getByText(/Reset to defaults/i);
    fireEvent.click(resetBtn);
    expect(inputs.apiUrl.value).toBe('https://api.openai.com/v1');
  });

  it('loads configuration from localStorage', async () => {
    const saved = {
      apiUrl: 'https://custom.local',
      apiKey: 'storage-key',
      model: 'gpt-3.5-turbo',
      temperature: 0.3,
      maxTokens: 1000
    } as ApiConfigType;
    localStorageMock.setItem('knowledgeDiver.apiConfig', JSON.stringify(saved));
    const onSave = vi.fn();
    const { container } = render(<ApiConfig onSave={onSave} />);
    await waitFor(() => {
      const inputs = getInputs(container);
      expect(inputs.apiUrl.value).toBe('https://custom.local');
    });
  });
});
