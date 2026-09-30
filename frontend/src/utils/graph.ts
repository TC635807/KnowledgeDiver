import { Card } from '../types/card';

export interface GraphNode {
  id: string;
  label: string;
  color?: string;
  size?: number;
}

export interface GraphEdge {
  id?: string;
  from: string;
  to: string;
  color?: string;
}

export function cardsToGraph(cards: Card[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = cards.map((card) => ({
    id: card.id,
    label: card.title,
    color: undefined,
    size: 25,
  }));

  const edges: GraphEdge[] = [];
  const edgeSet = new Set<string>();

  const addEdge = (from: string, to: string) => {
    const key = `${from}->${to}`;
    const reverseKey = `${to}->${from}`;
    if (!edgeSet.has(key) && !edgeSet.has(reverseKey)) {
      edgeSet.add(key);
      // Use a stable ID based on the connection so incremental updates
      // can correctly match edges across re-renders.
      edges.push({ id: key, from, to, color: '#9aa5b1' });
    }
  };

  for (const card of cards) {
    if (card.links) {
      for (const linkId of card.links) {
        if (cards.some(c => c.id === linkId)) {
          addEdge(card.id, linkId);
        }
      }
    }
  }

  return { nodes, edges };
}

export function getConnectedNodes(cardId: string, cards: Card[]): string[] {
  const connected = new Set<string>();
  const card = cards.find(c => c.id === cardId);
  if (!card) return [];

  if (card.links) {
    for (const linkId of card.links) {
      if (cards.some(c => c.id === linkId)) connected.add(linkId);
    }
  }
  if (card.backlinks) {
    for (const backlinkId of card.backlinks) {
      if (cards.some(c => c.id === backlinkId)) connected.add(backlinkId);
    }
  }

  return Array.from(connected);
}
