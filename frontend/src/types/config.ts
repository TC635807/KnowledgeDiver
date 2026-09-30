export interface ApiConfig {
  apiUrl: string;
  apiKey: string;
  model: string;
  temperature: number;
  maxTokens: number;
  proxyPort: number;
}

export const DEFAULT_CONFIG: ApiConfig = {
  apiUrl: 'https://api.deepseek.com',
  apiKey: '',
  model: 'deepseek-v4-flash',
  temperature: 0.7,
  maxTokens: 4096,
  proxyPort: 0
};
