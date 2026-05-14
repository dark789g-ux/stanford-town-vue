// Builds per-direction frame arrays for one persona from atlas.json + the
// persona's character PNG. All 25 character sheets share the same atlas layout
// (96x128, 32x32 frames), so atlas.json is loaded/parsed once and reused.

import { Assets, Rectangle, Texture } from 'pixi.js'
import type { AtlasJson, CharacterSheet, Direction } from './types'

const DIRECTIONS: Direction[] = ['down', 'left', 'right', 'up']

/** Convert a persona display name ("Isabella Rodriguez") to its file stem. */
export function personaToFile(name: string): string {
  return name.trim().replace(/\s+/g, '_')
}

/**
 * Load + parse atlas.json once. Subsequent calls return the cached promise.
 * The atlas is plain JSON (frame rectangles), not a Pixi spritesheet, so we
 * fetch it directly rather than via Assets — it describes a layout shared by
 * many separate base textures.
 */
let atlasPromise: Promise<AtlasJson> | null = null
export function loadAtlas(atlasUrl: string): Promise<AtlasJson> {
  if (!atlasPromise) {
    atlasPromise = fetch(atlasUrl).then((r) => {
      if (!r.ok) throw new Error(`Failed to load atlas.json: ${r.status}`)
      return r.json() as Promise<AtlasJson>
    })
  }
  return atlasPromise
}

/** Test seam: reset the atlas cache (used only by the dev harness / tests). */
export function _resetAtlasCache(): void {
  atlasPromise = null
}

/**
 * Build a CharacterSheet for a persona.
 *
 * @param name        Persona display name, e.g. "Klaus Mueller".
 * @param charBaseUrl Base URL for character PNGs, e.g. "/assets/characters".
 * @param atlasUrl    Full URL to atlas.json.
 */
export async function loadCharacterSheet(
  name: string,
  charBaseUrl: string,
  atlasUrl: string,
): Promise<CharacterSheet> {
  const atlas = await loadAtlas(atlasUrl)
  const pngUrl = `${charBaseUrl}/${personaToFile(name)}.png`
  const baseTexture: Texture = await Assets.load(pngUrl)
  const source = baseTexture.source

  // Index atlas frames by filename for quick lookup.
  const byName = new Map<string, AtlasJson['frames'][number]>()
  for (const f of atlas.frames) byName.set(f.filename, f)

  const sub = (filename: string): Texture => {
    const f = byName.get(filename)
    if (!f) {
      throw new Error(`atlas.json is missing frame "${filename}"`)
    }
    return new Texture({
      source,
      frame: new Rectangle(f.frame.x, f.frame.y, f.frame.w, f.frame.h),
    })
  }

  const walk = {} as Record<Direction, Texture[]>
  const idle = {} as Record<Direction, Texture>

  for (const dir of DIRECTIONS) {
    // atlas defines {dir}-walk.000..003 (003 == 001 -> natural 0,1,2,1 cycle)
    walk[dir] = [
      sub(`${dir}-walk.000`),
      sub(`${dir}-walk.001`),
      sub(`${dir}-walk.002`),
      sub(`${dir}-walk.003`),
    ]
    // bare "{dir}" frame is the static / idle pose
    idle[dir] = sub(dir)
  }

  return { walk, idle }
}
