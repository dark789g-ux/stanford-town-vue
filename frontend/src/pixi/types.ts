// Internal types for the Pixi rendering engine.
// Public-facing types live in ./index.ts (the frozen public API surface).

import type { Texture } from 'pixi.js'

/** World / tile geometry constants for the_ville map. */
export const TILE_SIZE = 32
export const WORLD_TILES_W = 140
export const WORLD_TILES_H = 100
export const WORLD_PX_W = WORLD_TILES_W * TILE_SIZE // 4480
export const WORLD_PX_H = WORLD_TILES_H * TILE_SIZE // 3200

/** Camera zoom clamps. */
export const MIN_ZOOM = 0.25
export const MAX_ZOOM = 2

/** The four facing directions an agent sprite can take. */
export type Direction = 'down' | 'left' | 'right' | 'up'

/** Per-direction texture frame arrays built from atlas.json + a character PNG. */
export interface CharacterSheet {
  /** Walk-cycle frames keyed by direction (4 frames each: 0,1,2,1). */
  walk: Record<Direction, Texture[]>
  /** Single idle/static frame keyed by direction. */
  idle: Record<Direction, Texture>
}

/** Raw shape of atlas.json (Atlas Packer Gamma V2 / TexturePacker-ish). */
export interface AtlasFrame {
  filename: string
  frame: { x: number; y: number; w: number; h: number }
  anchor?: { x: number; y: number }
}
export interface AtlasJson {
  frames: AtlasFrame[]
  meta?: Record<string, unknown>
}
