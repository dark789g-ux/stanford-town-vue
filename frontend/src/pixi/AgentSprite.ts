// One agent in the town: an AnimatedSprite walk cycle + a name label + an
// optional emoji speech bubble. Owns its own tween between tiles; the renderer
// ticks it each frame and retargets it on each setStep().

import { AnimatedSprite, Container, Graphics, Text } from 'pixi.js'
import type { CharacterSheet, Direction } from './types'
import { TILE_SIZE } from './types'

/** Pixels-per-second the agent walks at while tweening between tiles. */
const WALK_SPEED_PX = 96
/** Walk animation playback speed (frames advanced per Pixi tick unit). */
const ANIM_SPEED = 0.18

/** tile coord -> pixel coord (tile centre). */
function tileToPx(tile: number): number {
  return tile * TILE_SIZE + TILE_SIZE / 2
}

export class AgentSprite {
  /** Root container; positioned in world space by the renderer's world layer. */
  readonly view: Container
  /** Persona display name. */
  readonly name: string

  private sprite: AnimatedSprite
  private label: Text
  private bubble: Container
  private bubbleText: Text
  private bubbleBg: Graphics
  private sheet: CharacterSheet

  private dir: Direction = 'down'
  private moving = false
  /** Current world-pixel position (sprite anchor point). */
  private px: number
  private py: number
  /** Tween target in world pixels. */
  private targetX: number
  private targetY: number

  constructor(
    name: string,
    sheet: CharacterSheet,
    tileX: number,
    tileY: number,
    onClick?: (name: string) => void,
  ) {
    this.name = name
    this.sheet = sheet
    this.px = tileToPx(tileX)
    this.py = tileToPx(tileY)
    this.targetX = this.px
    this.targetY = this.py

    this.view = new Container()
    this.view.eventMode = 'static'
    this.view.cursor = 'pointer'
    if (onClick) {
      this.view.on('pointertap', () => onClick(this.name))
    }

    // --- walk sprite -------------------------------------------------------
    this.sprite = new AnimatedSprite(sheet.idle.down ? [sheet.idle.down] : sheet.walk.down)
    this.sprite.anchor.set(0.5, 0.5)
    this.sprite.animationSpeed = ANIM_SPEED
    this.view.addChild(this.sprite)

    // --- name label --------------------------------------------------------
    this.label = new Text({
      text: name,
      style: {
        fontFamily: 'Arial, sans-serif',
        fontSize: 9,
        fill: 0xffffff,
        stroke: { color: 0x000000, width: 3 },
        align: 'center',
      },
    })
    this.label.anchor.set(0.5, 1)
    this.label.position.set(0, -TILE_SIZE / 2 - 2)
    this.label.scale.set(0.75)
    this.view.addChild(this.label)

    // --- speech bubble (emoji pronunciatio) --------------------------------
    this.bubble = new Container()
    this.bubbleBg = new Graphics()
    this.bubbleText = new Text({
      text: '',
      style: { fontFamily: 'Arial, sans-serif', fontSize: 14, fill: 0x000000 },
    })
    this.bubbleText.anchor.set(0.5, 0.5)
    this.bubble.addChild(this.bubbleBg)
    this.bubble.addChild(this.bubbleText)
    this.bubble.position.set(0, -TILE_SIZE / 2 - 14)
    this.bubble.visible = false
    this.view.addChild(this.bubble)

    this.applyIdleFrame()
    this.syncView()
  }

  /** World-pixel y of the agent's feet — used by the renderer for y-sorting. */
  get sortY(): number {
    return this.py
  }

  /** Retarget the tween to a new tile. Direction is derived from the delta. */
  moveTo(tileX: number, tileY: number): void {
    this.targetX = tileToPx(tileX)
    this.targetY = tileToPx(tileY)
    const dx = this.targetX - this.px
    const dy = this.targetY - this.py
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) {
      this.stopMoving()
      return
    }
    this.dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up'
    if (!this.moving) {
      this.moving = true
      this.sprite.textures = this.sheet.walk[this.dir]
      this.sprite.play()
    } else {
      // direction may have changed mid-tween
      this.sprite.textures = this.sheet.walk[this.dir]
      this.sprite.play()
    }
  }

  /** Set (or clear) the emoji speech bubble. */
  setPronunciatio(pronunciatio: string | null): void {
    if (!pronunciatio) {
      this.bubble.visible = false
      return
    }
    this.bubble.visible = true
    this.bubbleText.text = pronunciatio
    const w = Math.max(this.bubbleText.width + 8, 18)
    const h = this.bubbleText.height + 6
    this.bubbleBg.clear()
    this.bubbleBg.roundRect(-w / 2, -h / 2, w, h, 4).fill(0xffffff).stroke({ color: 0x333333, width: 1 })
    // keep text drawn above the freshly-cleared background
    this.bubble.setChildIndex(this.bubbleText, this.bubble.children.length - 1)
  }

  /** Per-frame update. `dtSec` is elapsed seconds since the last tick. */
  update(dtSec: number): void {
    if (this.moving) {
      const dx = this.targetX - this.px
      const dy = this.targetY - this.py
      const dist = Math.hypot(dx, dy)
      const stepPx = WALK_SPEED_PX * dtSec
      if (dist <= stepPx || dist < 0.5) {
        this.px = this.targetX
        this.py = this.targetY
        this.stopMoving()
      } else {
        this.px += (dx / dist) * stepPx
        this.py += (dy / dist) * stepPx
      }
      this.syncView()
    }
  }

  /** Free GPU references for this agent. Frame textures are owned by the sheet. */
  destroy(): void {
    this.view.destroy({ children: true })
  }

  // --- internals -----------------------------------------------------------

  private stopMoving(): void {
    this.moving = false
    this.sprite.stop()
    this.applyIdleFrame()
  }

  private applyIdleFrame(): void {
    this.sprite.textures = [this.sheet.idle[this.dir]]
    this.sprite.gotoAndStop(0)
  }

  private syncView(): void {
    this.view.position.set(this.px, this.py)
    // zIndex drives the renderer's sortableChildren y-ordering
    this.view.zIndex = this.py
  }
}
