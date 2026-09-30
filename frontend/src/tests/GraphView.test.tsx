import React from 'react';
import { render } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import GraphView from '../components/GraphView';
import { Card } from '../types/card';
import { cardsToGraph } from '../utils/graph';

// jsdom does not provide ResizeObserver
(globalThis as any).ResizeObserver = class {
  observe() {}
  disconnect() {}
  unobserve() {}
};

vi.mock('vis-network/standalone', () => {
  class DataSet {
    public items: any[];
    constructor(items: any[]) {
      this.items = items;
    }
    update(items: any[]) {
      this.items = items;
    }
    get(id?: string) {
      if (id) return this.items.find((i: any) => i.id === id) || null;
      return this.items;
    }
    getIds() {
      return this.items.map((i: any) => i.id);
    }
    add(items: any[]) {
      this.items.push(...items);
    }
    remove(ids: string[]) {
      this.items = this.items.filter((i: any) => !ids.includes(i.id));
    }
  }
  class Network {
    static lastInstance: Network | null = null;
    public container: any;
    public data: any;
    public options: any;
    public _on: Record<string, Function> = {};
    public _once: Record<string, Function> = {};
    constructor(container: any, data: any, options: any) {
      this.container = container;
      this.data = data;
      this.options = options;
      Network.lastInstance = this;
    }
    on(event: string, cb: Function) {
      this._on[event] = cb;
    }
    once(event: string, cb: Function) {
      this._once[event] = cb;
    }
    selectNodes(ids: string[]) {}
    getPosition(id: string) { return { x: 100, y: 200 }; }
    focus(id: string, opts: any) {}
    moveTo(opts: any) {}
    fit(opts: any) {}
    setSize(w: string, h: string) {}
    redraw() {}
    setOptions(opts: any) {}
    setData(data: any) {}
    destroy() {}
    stabilize() {}
    triggerClick(nodes: string[]) {
      const cb = this._on['click'];
      cb && cb({ nodes });
    }
    triggerStabilized() {
      const cb = this._once['stabilized'];
      cb && cb();
    }
  }
  return {
    __esModule: true,
    default: Network,
    Network,
    DataSet,
  };
});

describe('GraphView', () => {
  it('renders graph with cards and creates nodes/edges', () => {
    const cards: Card[] = [
      { id: 'c1', title: 'Card One', content: 'Content for card one' },
      { id: 'c2', title: 'Card Two', content: 'Content for card two' },
      { id: 'c3', title: 'Card Three', content: 'Content for card three' },
    ];
    const onSelect = vi.fn();
    const { container } = render(<GraphView cards={cards} selectedId={null} onSelectCard={onSelect} visible={true} />);
    expect(container.firstChild).toBeTruthy();
  });

  it('handles node click and calls onSelectCard', async () => {
    const cards: Card[] = [
      { id: 'c1', title: 'Card One', content: 'Content for card one' },
      { id: 'c2', title: 'Card Two', content: 'Content for card two' },
    ];
    const onSelect = vi.fn();
    render(<GraphView cards={cards} selectedId={null} onSelectCard={onSelect} visible={true} />);

    const visNetwork = await import('vis-network/standalone');
    const Network = (visNetwork as any).Network ?? (visNetwork as any).default;
    const net = Network.lastInstance;
    expect(net).toBeTruthy();
    net.triggerClick(['c1']);
    expect(onSelect).toHaveBeenCalledWith('c1');
  });

  it('cardsToGraph converts cards to nodes and edges', () => {
    const cards = [
      { id: 'a', title: 'A', content: 'Content A', links: ['b'] },
      { id: 'b', title: 'B', content: 'Content B', links: ['c'] },
      { id: 'c', title: 'C', content: 'Content C' },
    ];
    const { nodes, edges } = cardsToGraph(cards as Card[]);
    expect(nodes.length).toBe(3);
    expect(edges.length).toBe(2);
  });

  it('renders empty state gracefully with no cards', () => {
    const cards: Card[] = [];
    const onSelect = vi.fn();
    render(<GraphView cards={cards} selectedId={null} onSelectCard={onSelect} visible={true} />);
    const divs = document.querySelectorAll('.graph-view-container');
    expect(divs.length).toBe(0);
    const emptyDivs = document.querySelectorAll('.graph-view-empty');
    expect(emptyDivs.length).toBeGreaterThanOrEqual(1);
  });
});
