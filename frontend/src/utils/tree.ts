import { Card } from '../types/card';

export type SortMode = 'tree' | 'newest' | 'alpha';

export interface TreeNode {
  id: string;
  card: Card;
  children: TreeNode[];
  isExpanded: boolean;
}

/**
 * Build a tree hierarchy from a flat card list.
 * In "tree" mode, uses links/backlinks to build parent-child relationships
 * (cards with links pointing to other cards are parents of those cards).
 * In "newest" / "alpha" mode, returns a flat list sorted accordingly.
 */
export function buildTree(cards: Card[], sortMode: SortMode = 'tree'): TreeNode[] {
  if (sortMode === 'tree') {
    return _buildHierarchy(cards);
  }
  return _buildFlat(cards, sortMode);
}

function _sortCards(cards: Card[], mode: SortMode): Card[] {
  const sorted = [...cards];
  if (mode === 'newest') {
    sorted.sort((a, b) => {
      const da = a.created_at ? new Date(a.created_at).getTime() : 0;
      const db = b.created_at ? new Date(b.created_at).getTime() : 0;
      return db - da;
    });
  } else if (mode === 'alpha') {
    sorted.sort((a, b) => a.title.localeCompare(b.title, 'zh-CN'));
  }
  return sorted;
}

function _buildFlat(cards: Card[], sortMode: SortMode): TreeNode[] {
  return _sortCards(cards, sortMode).map(card => ({
    id: card.id,
    card,
    children: [],
    isExpanded: false,
  }));
}

function _buildHierarchy(cards: Card[]): TreeNode[] {
  const cardMap = new Map(cards.map(c => [c.id, c]));
  const cardIds = new Set(cards.map(c => c.id));

  // 树方向优先显式 parent_id（后端已迁移到无向链接 + parent_id）；
  // 存量数据无 parent_id 时回退 links 推断（含连通分量断环）。
  const hasParentIds = cards.some(c => c.parent_id);
  if (hasParentIds) {
    const childrenMap = new Map<string, string[]>();
    for (const c of cards) {
      if (c.parent_id && cardIds.has(c.parent_id)) {
        const list = childrenMap.get(c.parent_id) || [];
        list.push(c.id);
        childrenMap.set(c.parent_id, list);
      }
    }
    // 根 = 未设父卡 或 父卡不在卡片集（悬空引用）
    const roots = cards.filter(c => !c.parent_id || !cardIds.has(c.parent_id));

    function buildNode(card: Card, visited: Set<string> = new Set()): TreeNode {
      if (visited.has(card.id)) {
        return { id: card.id, card, children: [], isExpanded: true };
      }
      const nextVisited = new Set(visited);
      nextVisited.add(card.id);
      const childIds = childrenMap.get(card.id) || [];
      const childCards = childIds
        .map(id => cardMap.get(id))
        .filter((c): c is Card => c != null);
      return {
        id: card.id,
        card,
        children: _sortCards(childCards, 'newest').map(c => buildNode(c, nextVisited)),
        isExpanded: true,
      };
    }

    return _sortCards(roots, 'newest').map(c => buildNode(c));
  }

  // childrenMap: parentId → [childId], inDegree: nodeId → incoming link count
  const childrenMap = new Map<string, string[]>();
  const inDegree = new Map<string, number>();
  for (const c of cards) inDegree.set(c.id, 0);

  for (const card of cards) {
    const children: string[] = [];
    for (const linkedId of card.links || []) {
      if (cardIds.has(linkedId)) {
        children.push(linkedId);
        inDegree.set(linkedId, (inDegree.get(linkedId) || 0) + 1);
      }
    }
    if (children.length > 0) childrenMap.set(card.id, children);
  }

  // Root nodes: cards with zero incoming links (DAG root detection)
  const roots = cards.filter(c => (inDegree.get(c.id) || 0) === 0);

  // Break cycles per connected component: cards unreachable from any root
  // form pure-cycle components (all have in-degree > 0). Promote the card
  // with the most outgoing links in each component to root and strip its
  // incoming edges, so cyclic components render instead of vanishing.
  const reachable = new Set<string>();
  const stack = roots.map(c => c.id);
  while (stack.length) {
    const id = stack.pop()!;
    if (reachable.has(id)) continue;
    reachable.add(id);
    for (const childId of childrenMap.get(id) || []) stack.push(childId);
  }
  const unreachable = cards.filter(c => !reachable.has(c.id));
  if (unreachable.length > 0) {
    const unSet = new Set(unreachable.map(c => c.id));
    const neighbors = new Map<string, Set<string>>();
    for (const c of unreachable) {
      const nb = new Set<string>();
      for (const ch of childrenMap.get(c.id) || []) {
        if (unSet.has(ch)) nb.add(ch);
      }
      for (const p of unreachable) {
        if ((childrenMap.get(p.id) || []).includes(c.id)) nb.add(p.id);
      }
      neighbors.set(c.id, nb);
    }
    const seen = new Set<string>();
    for (const c of unreachable) {
      if (seen.has(c.id)) continue;
      const comp: string[] = [];
      const queue = [c.id];
      seen.add(c.id);
      while (queue.length) {
        const id = queue.shift()!;
        comp.push(id);
        for (const nb of neighbors.get(id) || []) {
          if (!seen.has(nb)) {
            seen.add(nb);
            queue.push(nb);
          }
        }
      }
      const breaker = comp.sort(
        (x, y) => (childrenMap.get(y)?.length || 0) - (childrenMap.get(x)?.length || 0)
      )[0];
      for (const id of comp) {
        childrenMap.set(id, (childrenMap.get(id) || []).filter(ch => ch !== breaker));
      }
      const breakerCard = cards.find(c => c.id === breaker);
      if (breakerCard) roots.push(breakerCard);
    }
  }

  function buildNode(card: Card, visited: Set<string> = new Set()): TreeNode {
    // Guard: prevent infinite recursion on residual cycles
    if (visited.has(card.id)) {
      return { id: card.id, card, children: [], isExpanded: true };
    }
    const nextVisited = new Set(visited);
    nextVisited.add(card.id);

    const childIds = childrenMap.get(card.id) || [];
    const childCards = childIds
      .map(id => cardMap.get(id))
      .filter((c): c is Card => c != null);

    return {
      id: card.id,
      card,
      children: _sortCards(childCards, 'newest').map(c => buildNode(c, nextVisited)),
      isExpanded: true,
    };
  }

  return _sortCards(roots, 'newest').map(c => buildNode(c));
}

export function getAncestors(cardId: string, cards: Card[]): Card[] {
  return [];
}

export function getDescendants(cardId: string, cards: Card[]): Card[] {
  return [];
}
