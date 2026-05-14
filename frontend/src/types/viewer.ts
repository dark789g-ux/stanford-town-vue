// Mirrors the Pixi engine's AgentFrame/StepFrame so the store can hand frames
// straight to the renderer.
export interface AgentFrame {
  name: string
  x: number
  y: number
  pronunciatio: string | null
  description: string | null
}

export interface StepFrame {
  step: number
  curr_time: string
  agents: AgentFrame[]
}

export type ViewerMode = 'live' | 'replay'
