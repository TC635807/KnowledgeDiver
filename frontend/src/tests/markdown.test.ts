import { describe, it, expect } from 'vitest';
import { sanitizeUrl } from '../utils/markdown';

describe('sanitizeUrl', () => {
  it('rejects javascript: protocol', () => {
    expect(sanitizeUrl('javascript:alert(1)')).toBe('#');
  });

  it('rejects data: protocol', () => {
    expect(sanitizeUrl('data:text/html,<script>alert(1)</script>')).toBe('#');
  });

  it('rejects vbscript: protocol', () => {
    expect(sanitizeUrl('vbscript:msgbox(1)')).toBe('#');
  });

  it('allows https: protocol', () => {
    expect(sanitizeUrl('https://example.com')).toBe('https://example.com');
  });

  it('allows http: protocol', () => {
    expect(sanitizeUrl('http://example.com')).toBe('http://example.com');
  });

  it('allows mailto: protocol', () => {
    expect(sanitizeUrl('mailto:test@test.com')).toBe('mailto:test@test.com');
  });

  it('allows relative paths starting with /', () => {
    expect(sanitizeUrl('/wiki/page')).toBe('/wiki/page');
  });

  it('allows anchor links', () => {
    expect(sanitizeUrl('#section')).toBe('#section');
  });

  it('allows relative paths starting with ./', () => {
    expect(sanitizeUrl('./image.png')).toBe('./image.png');
  });

  it('rejects empty string', () => {
    expect(sanitizeUrl('')).toBe('#');
  });
});
