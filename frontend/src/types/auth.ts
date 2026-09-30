export interface AuthUser {
  username: string
  created_at: string
  avatar_url?: string | null
}

export interface AuthResponse {
  access_token: string
  token_type: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  password: string
}
