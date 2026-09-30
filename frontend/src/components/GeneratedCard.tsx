import React from 'react'
import type { Card } from '../types/card'

export const GeneratedCard: React.FC<{ card: Card }> = ({ card }) => {
  const contentPreview = (card.content ?? '').slice(0, 180)

  return (
    <div className="generated-card" role="button" aria-label={`Card ${card.title}`}>
      <div className="card-title">{card.title}</div>
      <div className="card-content">{contentPreview}...</div>
      <div className="card-meta">
        {(card.metadata && Object.entries(card.metadata)).map(([k, v]) => (
          <span key={k} className="card-meta-item">{k}: {v}</span>
        ))}
      </div>
    </div>
  )
}
