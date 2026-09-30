import React from 'react';
import { parseMarkdown } from '../utils/markdown';

type Props = {
  content: string;
};

const MarkdownPreview: React.FC<Props> = ({ content }) => {
  const html = parseMarkdown(content);
  return (
    <div className="markdown-preview" dangerouslySetInnerHTML={{ __html: html }} />
  );
};

export default MarkdownPreview;
