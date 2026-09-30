import type { Card } from './card'

export interface HubComment {
  username: string
  content: string
  created_at: string
}

export interface HubManifest {
  name: string
  description: string
  creator: string
  created_at: string
  updated_at: string
  card_count: number
  topics: string[]
  graph: {
    nodes: { id: string; label: string }[]
    edges: { from: string; to: string }[]
  }
  likes: number
  dislikes: number
  liked_by: string[]
  disliked_by: string[]
  comments: HubComment[]
}

export interface HubSessionSummary {
  name: string
  description: string
  creator: string
  created_at: string
  updated_at: string
  card_count: number
  topics: string[]
  likes: number
  dislikes: number
  comment_count: number
}

export interface HubSessionDetail {
  manifest: HubManifest
  cards: Card[]
}

export type HubSortBy = 'newest' | 'most_likes' | 'trending'
