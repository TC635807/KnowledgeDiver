import React, { useEffect, useRef, useMemo } from 'react';
import { DataSet, Network } from 'vis-network/standalone';
import { Card } from '../types/card';
import { cardsToGraph } from '../utils/graph';
import './GraphView.css';

type GraphViewProps = {
  cards: Card[];
  selectedId: string | null;
  onSelectCard: (id: string) => void;
  onOpenCard?: (id: string) => void;
  visible: boolean;
};

const GraphView: React.FC<GraphViewProps> = ({ cards, selectedId, onSelectCard, onOpenCard, visible }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const nodesRef = useRef<DataSet<any> | null>(null);
  const edgesRef = useRef<DataSet<any> | null>(null);
  const prevCardIdsRef = useRef<Set<string>>(new Set());
  const onSelectCardRef = useRef(onSelectCard);
  const onOpenCardRef = useRef(onOpenCard);
  const userInteractedRef = useRef(false);
  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Keep callback ref fresh without triggering effect re-runs
  onSelectCardRef.current = onSelectCard;
  onOpenCardRef.current = onOpenCard;

  // Stable key from sorted card IDs — only changes when the set of card identities changes
  const cardIdsKey = useMemo(() => {
    return cards.map(c => c.id).sort().join(',');
  }, [cards]);

  const cardIds = useMemo(() => {
    return new Set(cards.map(c => c.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardIdsKey]);

  // Build / update the network when the card set changes
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    if (cards.length === 0) {
      if (networkRef.current) {
        networkRef.current.destroy();
        networkRef.current = null;
        nodesRef.current = null;
        edgesRef.current = null;
        prevCardIdsRef.current = new Set();
      }
      return;
    }

    const { nodes, edges } = cardsToGraph(cards);
    if (nodes.length === 0) return;

    if (!networkRef.current) {
      container.innerHTML = '';

      nodesRef.current = new DataSet(nodes);
      edgesRef.current = new DataSet(edges);

      const data = {
        nodes: nodesRef.current,
        edges: edgesRef.current,
      };

      const style = getComputedStyle(document.documentElement);
      const accent = style.getPropertyValue('--accent').trim() || '#8b5cf6';
      const accentLight = style.getPropertyValue('--accent-light').trim() || '#a78bfa';
      const accentHover = style.getPropertyValue('--accent-hover').trim() || '#7c3aed';
      const textColor = style.getPropertyValue('--text').trim() || '#e4e2f0';
      const bgColor = style.getPropertyValue('--bg').trim() || '#05050f';
      const textMuted = style.getPropertyValue('--text-muted').trim() || '#5c5878';

      const options = {
        autoResize: true,
        height: '100%',
        width: '100%',
        physics: {
          enabled: true,
          stabilization: {
            enabled: true,
            iterations: 800,
            updateInterval: 25,
            onlyDynamicEdges: false,
          },
          solver: 'forceAtlas2Based',
          forceAtlas2Based: {
            gravitationalConstant: -80,
            centralGravity: 0.001,
            springLength: 180,
            springConstant: 0.04,
            damping: 0.7,
          },
        },
        interaction: {
          hover: true,
          navigationButtons: true,
          zoomView: true,
          dragView: true,
        },
        nodes: {
          shape: 'dot',
          size: 20,
          font: {
            color: textColor,
            strokeColor: bgColor,
            strokeWidth: 0,
            size: 12,
            face: 'system-ui, -apple-system, sans-serif',
            bold: 'normal',
          },
          labelHighlightBold: false,
          color: {
            background: accentHover,
            border: accentLight,
            highlight: {
              background: accentLight,
              border: accent,
            },
            hover: {
              background: accentLight,
              border: accent,
            },
          },
          borderWidth: 2,
          shadow: {
            enabled: true,
            color: `${accent}4d`,
            size: 10,
            x: 0,
            y: 0,
          },
        },
        edges: {
          color: {
            color: textMuted,
            highlight: accentLight,
            hover: accent,
          },
          width: 1.5,
          smooth: {
            enabled: true,
            type: 'continuous',
            roundness: 0.5,
          },
        },
      };

      const network = new Network(container, data, options);
      networkRef.current = network;

      network.on('click', (params: any) => {
        const nodeId = params?.nodes?.[0];
        if (nodeId) {
          userInteractedRef.current = true;
          // Delay single-click handling to distinguish from double-click.
          // A double-click fires 'click' first, then 'doubleClick'; if we
          // select immediately, the camera animation jitters right before
          // the window opens. Deferring by ~250ms lets double-click win.
          if (clickTimerRef.current) {
            clearTimeout(clickTimerRef.current);
          }
          clickTimerRef.current = setTimeout(() => {
            clickTimerRef.current = null;
            onSelectCardRef.current(nodeId);
          }, 250);
        }
      });

      network.on('doubleClick', (params: any) => {
        const nodeId = params?.nodes?.[0];
        if (nodeId) {
          // Cancel the pending single-click so we don't also trigger select-focus
          if (clickTimerRef.current) {
            clearTimeout(clickTimerRef.current);
            clickTimerRef.current = null;
          }
          userInteractedRef.current = true;
          onOpenCardRef.current?.(nodeId);
        }
      });

      network.once('stabilized', () => {
        if (userInteractedRef.current) return;
        network.fit({
          animation: {
            duration: 500,
            easingFunction: 'easeInOutQuad',
          },
        });
      });

      prevCardIdsRef.current = cardIds;
    } else {
      const prevIds = prevCardIdsRef.current;
      const newIds = cardIds;

      const addedIds = Array.from(newIds).filter((id): id is string => !prevIds.has(id));
      const removedIds = Array.from(prevIds).filter((id): id is string => !newIds.has(id));

      if (addedIds.length > 0) {
        const addedNodes = nodes.filter(n => addedIds.includes(n.id));
        nodesRef.current?.add(addedNodes);
      }

      if (removedIds.length > 0) {
        nodesRef.current?.remove(removedIds);
      }

      const newEdgeIds = new Set(edges.map(e => e.id));
      const existingEdgeIds = new Set(edgesRef.current?.getIds() || []);
      const addedEdges = edges.filter(e => !existingEdgeIds.has(e.id));
      const removedEdges = Array.from(existingEdgeIds).filter(id => !newEdgeIds.has(String(id)));

      if (addedEdges.length > 0) {
        edgesRef.current?.add(addedEdges);
      }

      if (removedEdges.length > 0) {
        edgesRef.current?.remove(removedEdges);
      }

      prevCardIdsRef.current = cardIds;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardIdsKey]);

  // Cleanup network on unmount
  useEffect(() => {
    return () => {
      if (clickTimerRef.current) {
        clearTimeout(clickTimerRef.current);
      }
      if (networkRef.current) {
        networkRef.current.destroy();
        networkRef.current = null;
        nodesRef.current = null;
        edgesRef.current = null;
        prevCardIdsRef.current = new Set();
        userInteractedRef.current = false;
      }
      const container = containerRef.current;
      if (container) {
        container.innerHTML = '';
      }
    };
  }, []);

  // Animate camera to selected node WITHOUT toggling physics
  // This is the key fix: physics stays enabled, we just move the camera.
  // No animatingRef guard — moveTo's own animation handles rapid re-targeting,
  // and the old guard could permanently block focus (stuck animatingRef=true)
  // when selectedId changed before the RAF fired or inside the animation window.
  useEffect(() => {
    const network = networkRef.current;
    if (!network || !selectedId) return;

    const raf = requestAnimationFrame(() => {
      const net = networkRef.current;
      if (!net) return;

      net.selectNodes([selectedId]);

      const pos = net.getPosition(selectedId);
      net.moveTo({
        position: { x: pos.x, y: pos.y },
        scale: 1.5,
        animation: {
          duration: 400,
          easingFunction: 'easeInOutQuad',
        },
      });
    });

    return () => {
      cancelAnimationFrame(raf);
    };
  }, [selectedId]);

  // Handle container resize when panel becomes visible
  useEffect(() => {
    const network = networkRef.current;
    const container = containerRef.current;
    if (!network || !container || !visible) return;

    // Use ResizeObserver to detect when the panel has actually finished its CSS transition
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          network.setSize(`${width}px`, `${height}px`);
          network.redraw();
        }
      }
    });

    observer.observe(container);

    // Also do an immediate fit since the panel just became visible
    const fitTimer = setTimeout(() => {
      network.fit({
        animation: { duration: 200, easingFunction: 'easeInOutQuad' },
      });
    }, 100);

    return () => {
      observer.disconnect();
      clearTimeout(fitTimer);
    };
  }, [visible]);

  if (cards.length === 0) {
    return (
      <div className="graph-view-empty">
        <div className="graph-view-empty-icon">📊</div>
        <div className="graph-view-empty-text">暂无卡片</div>
        <div className="graph-view-empty-hint">创建卡片后查看知识图谱</div>
      </div>
    );
  }

  return (
    <div className="graph-view-wrapper">
      <div
        ref={containerRef}
        className="graph-view-container"
      />
    </div>
  );
};

export default GraphView;
