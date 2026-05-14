// The town rendering engine: owns the Pixi Application, the scene graph, and
// the ticker. Pure TypeScript — no Vue/Pinia. See ./index.ts for the frozen
// public API and ./__demo__.ts for a usage example.

import { Application, Assets, Container, Sprite } from 'pixi.js'
import { AgentSprite } from './AgentSprite'
import { Camera } from './Camera'
import { loadCharacterSheet } from './loadCharacterSheet'
import type { CharacterSheet } from './types'
import { TILE_SIZE } from './types'
import type { StepFrame, TownRendererOptions } from './index'

/** All 25 personas in the_ville — used to preload every character sheet. */
const PERSONA_NAMES: readonly string[] = [
  'Abigail Chen', 'Adam Smith', 'Arthur Burton', 'Ayesha Khan', 'Carlos Gomez',
  'Carmen Ortiz', 'Eddy Lin', 'Francisco Lopez', 'Giorgio Rossi', 'Hailey Johnson',
  'Isabella Rodriguez', 'Jane Moreno', 'Jennifer Moore', 'John Lin', 'Klaus Mueller',
  'Latoya Williams', 'Maria Lopez', 'Mei Lin', 'Rajiv Patel', 'Ryan Park',
  'Sam Moore', 'Tamara Taylor', 'Tom Moreno', 'Wolfgang Schulz', 'Yuriko Yamamoto',
]

export class TownRenderer {
  /** True once load() has resolved. */
  ready = false

  private readonly canvas: HTMLCanvasElement
  private readonly assetBaseUrl: string
  private readonly onAgentClick?: (name: string) => void

  private app: Application
  private camera!: Camera
  /** Layer order: ground -> agentLayer -> foreground. All inside camera.world. */
  private agentLayer!: Container
  private ground?: Sprite
  private foreground?: Sprite

  /** persona name -> loaded character sheet. */
  private sheets = new Map<string, CharacterSheet>()
  /** persona name -> live AgentSprite. */
  private agents = new Map<string, AgentSprite>()

  private loadPromise: Promise<void> | null = null
  private destroyed = false

  constructor(opts: TownRendererOptions) {
    this.canvas = opts.canvas
    this.assetBaseUrl = (opts.assetBaseUrl ?? '/assets').replace(/\/$/, '')
    this.onAgentClick = opts.onAgentClick
    this.app = new Application()
  }

  /** Load ground/foreground/character textures + atlas. Idempotent. */
  async load(): Promise<void> {
    if (this.loadPromise) return this.loadPromise
    this.loadPromise = this.doLoad()
    return this.loadPromise
  }

  private async doLoad(): Promise<void> {
    // Pixi v8 async init.
    await this.app.init({
      canvas: this.canvas,
      width: this.canvas.clientWidth || 800,
      height: this.canvas.clientHeight || 600,
      background: 0x1d1f2b,
      antialias: false,
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
    })
    if (this.destroyed) {
      this.app.destroy(true)
      return
    }

    const mazeUrl = `${this.assetBaseUrl}/maze/the_ville/visuals`
    const charBaseUrl = `${this.assetBaseUrl}/characters`
    const atlasUrl = `${charBaseUrl}/atlas.json`

    // Map layers + every character sheet, loaded in parallel.
    const [groundTex, foregroundTex] = await Promise.all([
      Assets.load(`${mazeUrl}/the_ville_ground.png`),
      Assets.load(`${mazeUrl}/the_ville_foreground.png`),
    ])
    await Promise.all(
      PERSONA_NAMES.map(async (name) => {
        const sheet = await loadCharacterSheet(name, charBaseUrl, atlasUrl)
        this.sheets.set(name, sheet)
      }),
    )
    if (this.destroyed) {
      this.app.destroy(true)
      return
    }

    // --- scene graph -------------------------------------------------------
    const world = new Container()
    this.app.stage.addChild(world)

    this.ground = new Sprite(groundTex)
    this.ground.position.set(0, 0)
    world.addChild(this.ground)

    this.agentLayer = new Container()
    this.agentLayer.sortableChildren = true // y-sort via each agent's zIndex
    world.addChild(this.agentLayer)

    this.foreground = new Sprite(foregroundTex)
    this.foreground.position.set(0, 0)
    world.addChild(this.foreground)

    // --- camera + input ----------------------------------------------------
    this.camera = new Camera(world, this.app.renderer.width, this.app.renderer.height)
    const stage = this.app.stage
    stage.eventMode = 'static'
    stage.hitArea = this.app.screen
    stage.on('pointerdown', this.camera.onPointerDown)
    stage.on('pointermove', this.camera.onPointerMove)
    stage.on('pointerup', this.camera.onPointerUp)
    stage.on('pointerupoutside', this.camera.onPointerUp)
    stage.on('wheel', this.camera.onWheel)

    // --- ticker ------------------------------------------------------------
    this.app.ticker.add((ticker) => {
      const dtSec = ticker.deltaMS / 1000
      for (const agent of this.agents.values()) agent.update(dtSec)
      this.agentLayer.sortChildren()
      this.camera.update()
    })

    this.ready = true
  }

  /**
   * Replace the rendered agent set for this step. Agents missing from the
   * frame are removed; new agents are added; everyone else is tweened toward
   * their new tile via AgentSprite.moveTo().
   */
  setStep(frame: StepFrame): void {
    if (!this.ready) return
    const seen = new Set<string>()

    for (const af of frame.agents) {
      seen.add(af.name)
      let agent = this.agents.get(af.name)
      if (!agent) {
        const sheet = this.sheets.get(af.name)
        if (!sheet) {
          // unknown persona (no character sheet) — skip rather than crash
          continue
        }
        agent = new AgentSprite(af.name, sheet, af.x, af.y, this.onAgentClick)
        this.agents.set(af.name, agent)
        this.agentLayer.addChild(agent.view)
      } else {
        agent.moveTo(af.x, af.y)
      }
      agent.setPronunciatio(af.pronunciatio)
    }

    // remove agents no longer present in the frame
    for (const [name, agent] of this.agents) {
      if (!seen.has(name)) {
        agent.destroy()
        this.agents.delete(name)
      }
    }
  }

  /** Camera follows the named agent; pass null to release. */
  focusOnAgent(name: string | null): void {
    if (!this.ready) return
    if (name === null) {
      this.camera.follow(null)
      return
    }
    const agent = this.agents.get(name)
    if (!agent) return
    this.camera.follow(() => ({ x: agent.view.position.x, y: agent.view.position.y }))
  }

  /** Set camera zoom (clamped in Camera to MIN_ZOOM..MAX_ZOOM). */
  setCameraZoom(zoom: number): void {
    if (!this.ready) return
    this.camera.setZoom(zoom)
  }

  /** Current zoom (for UI display). */
  getCameraZoom(): number {
    return this.ready ? this.camera.getZoom() : 1
  }

  /** Re-fit the renderer to a new canvas size. */
  resize(width: number, height: number): void {
    if (!this.app?.renderer) return
    this.app.renderer.resize(width, height)
    if (this.ready) {
      this.app.stage.hitArea = this.app.screen
      this.camera.resize(this.app.renderer.width, this.app.renderer.height)
    }
  }

  /** Tear down the Pixi Application + free GPU resources. */
  destroy(): void {
    this.destroyed = true
    for (const agent of this.agents.values()) agent.destroy()
    this.agents.clear()
    this.sheets.clear()
    if (this.app?.renderer) {
      // remove canvas:false — the canvas is owned by the Vue layer
      this.app.destroy(false, { children: true, texture: false })
    }
    this.ready = false
  }
}

/** Re-export for the unused tile constant so consumers can map coords if needed. */
export { TILE_SIZE }
