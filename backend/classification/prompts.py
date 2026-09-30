"""AI 卡片分类的 Prompt 模板。"""

CLASSIFY_PROMPT = (
    "You are an assistant tasked with classifying a knowledge card within a tree structure. "
    "Classify the given card under one of the candidate parent cards. "
    "Use the following fields to make a decision: card_id, title, content, metadata, and candidate_parents. "
    "Respond with JSON containing: 'suggested_parent_id' (string or null), 'confidence' (float 0.0-1.0), "
    "'reasoning' (string), and 'alternative_parents' (list of strings).\n\n"
    "Card details:\n"
    "- id: {card_id}\n"
    "- title: {title}\n"
    "- content: {content}\n"
    "- metadata: {metadata}\n"
    "Candidate parents:\n"
    "{candidates}\n"
    "Respond strictly with a JSON object as described."
)

"""CardClassifier 会填充卡片详情和候选父节点列表到此模板。"""
