// FROZEN PUBLIC API for the Pixi town rendering engine.
// This file is the ONLY public surface of src/pixi/. Everything else under
// src/pixi/ is internal. The Vue layer codes exclusively against these types.

/** One agent's state within a single simulation step. */
export interface AgentFrame {
  /** Persona display name, e.g. "Isabella Rodriguez". */
  name: string
  /** Tile x, 0..139. */
  x: number
  /** Tile y, 0..99. */
  y: number
  /** Emoji speech bubble, or null. */
  pronunciatio: string | null
  /** Current action description, or null. */
  description: string | null
}

/** A full simulation step: every agent's state at one moment. */
export interface StepFrame {
  step: number
  /** ISO-ish timestamp string — display only. */
  curr_time: string
  agents: AgentFrame[]
}

/** Constructor options for {@link TownRenderer}. */
export interface TownRendererOptions {
  /** The <canvas> the engine renders into (owned by the caller). */
  canvas: HTMLCanvasElement
  /** Base URL for assets; defaults to "/assets". */
  assetBaseUrl?: string
  /** Fired with the persona name when an agent sprite is clicked. */
  onAgentClick?: (name: string) => void
}

export { TownRenderer } from './TownRenderer'
