import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import TreeBrowser from '../components/TreeBrowser';
import { Card } from '../types/card';
import { buildTree, TreeNode } from '../utils/tree';

describe('TreeBrowser', () => {
  const cards: Card[] = [
    { id: 'root', title: 'Root', content: 'Root content', links: ['child1', 'child2'], backlinks: [] },
    { id: 'child1', title: 'Child 1', content: 'Child 1 content', links: [], backlinks: ['root'] },
    { id: 'child2', title: 'Child 2', content: 'Child 2 content', links: [], backlinks: ['root'] },
  ];

  it('renders root cards', () => {
    const onSelectCard = vi.fn();
    render(
      <TreeBrowser cards={cards} selectedId={null} onSelectCard={onSelectCard} />
    );
    expect(screen.getByText('Root')).toBeInTheDocument();
  });

  it('expands and collapses root node', () => {
    render(
      <TreeBrowser cards={cards} selectedId={null} onSelectCard={() => {}} />
    );
    const expandBtns = screen.getAllByRole('button', { name: '展开' });
    expect(expandBtns.length).toBeGreaterThan(0);
    expect(screen.queryByText('Child 1')).not.toBeInTheDocument();
    fireEvent.click(expandBtns[0]);
    expect(screen.getByText('Child 1')).toBeInTheDocument();
  });

  it('selects a card', () => {
    const onSelectCard = vi.fn();
    render(
      <TreeBrowser cards={cards} selectedId={null} onSelectCard={onSelectCard} />
    );
    const nodeRoot = screen.getByTestId('node-root');
    fireEvent.click(nodeRoot);
    expect(onSelectCard).toHaveBeenCalledWith('root');
  });

  it('buildTree utility creates a tree', () => {
    const tree = buildTree(cards);
    expect(tree.length).toBe(1);
    expect(tree[0].card.id).toBe('root');
    expect(tree[0].children.length).toBe(2);
  });

  it('buildTree keeps cyclic component when an isolated node exists', () => {
    // A<->B form a cycle (no in-degree-0 node); D is isolated.
    // Both the cycle breaker and D must appear as roots.
    const cycleCards: Card[] = [
      { id: 'a', title: 'A', content: '', links: ['b'], backlinks: ['b'] },
      { id: 'b', title: 'B', content: '', links: ['a'], backlinks: ['a'] },
      { id: 'd', title: 'D', content: '', links: [], backlinks: [] },
    ];
    const tree = buildTree(cycleCards);
    const ids = new Set<string>();
    const walk = (nodes: TreeNode[]) => {
      for (const n of nodes) {
        ids.add(n.id);
        walk(n.children);
      }
    };
    walk(tree);
    expect(ids).toEqual(new Set(['a', 'b', 'd']));
  });

  it('shows empty state when no cards', () => {
    render(
      <TreeBrowser cards={[]} selectedId={null} onSelectCard={() => {}} />
    );
    expect(screen.getByText(/暂无卡片/)).toBeInTheDocument();
  });
});
