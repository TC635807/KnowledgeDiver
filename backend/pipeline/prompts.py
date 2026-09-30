"""信息收集流水线的 AI Prompt 模板。

包含内容摘要、元数据提取、生成卡片结构等 Prompt 定义。
"""

# Summarize web content into a concise, KnowledgeDiver-friendly summary
SUMMARIZE_PROMPT = """
You are KnowledgeDiver's summarization assistant. Given the raw content, produce a concise, well-structured summary that highlights the key insights, evidence, and relevance to knowledge organization. Focus on clarity and usefulness for knowledge cards.
Input:
"""
"""\n{content}\n\nOutput: Summary text.\n"""

# Extract metadata such as year, category, and source-trust indicators from the content
EXTRACT_METADATA_PROMPT = """
Extract structured metadata from the content. Return a JSON object with fields like year (int, if determinable), category (string), author (string, if present), and source_trust (string: high/medium/low).
Input:
"""
"""\n{content}\n\nOutput: JSON with fields: year, category, author, source_trust.\n"""

# Generate a KnowledgeCard payload from summarized content
GENERATE_CARD_PROMPT = """
Create a knowledge card with a clear title, concise summary, and relevant metadata. Include the source URL and suggested tags.
Input:
title: {title}
summary: {summary}
metadata: {metadata}
source_url: {source_url}
Output: JSON with fields: title, content, source_url, tags (array of strings).
"""

EXTRACT_RELATED_TOPICS_PROMPT = """
Analyze the following content and identify related topics that are mentioned or referenced but deserve their own separate knowledge cards.

Rules:
- Topics should be distinct concepts, entities, or subjects (not just keywords)
- Topics should be specific enough to be useful (e.g., "法拉利" not "汽车品牌")
- Topics should be different from the main subject of this content
- Return ONLY a JSON array of topic names (strings), no explanations
- Maximum {max_topics} topics

Content:
{content}

JSON array of topics:"""
