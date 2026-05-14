// Pan / zoom / follow camera. Wraps a world-space Container: panning and
// zooming are applied as the container's position + scale. The container holds
// the ground sprite, agent layer, and foreground sprite.

import { Container, type FederatedPointerEvent, type FederatedWheelEvent } from 'pixi.js'
import { MAX_ZOOM, MIN_ZOOM, WORLD_PX_H, WORLD_PX_W } from './types'

/** Lerp factor per frame for smooth follow / focus recentre. */
const FOLLOW_LERP = 0.12

export class Camera {
  /** The world container that everything renders into. */
  readonly world: Container

  private viewW: number
  private viewH: number
  private zoom = 1

  /** Drag state. */
  private dragging = false
  private lastPointerX = 0
  private lastPointerY = 0

  /** When set, the camera lerps to keep this world point centred. */
  private followTargetGetter: (() => { x: number; y: number }) | null = null

  /** A one-shot smooth recentre target (world coords), cleared once reached. */
  private focusPoint: { x: number; y: number } | null = null

  constructor(world: Container, viewW: number, viewH: number) {
    this.world = world
    this.viewW = viewW
    this.viewH = viewH
    // start centred on the world middle
    this.centerOn(WORLD_PX_W / 2, WORLD_PX_H / 2, true)
  }

  // --- input handlers (wired up by TownRenderer on the stage) --------------

  onPointerDown = (e: FederatedPointerEvent): void => {
    this.dragging = true
    this.lastPointerX = e.global.x
    this.lastPointerY = e.global.y
  }

  onPointerMove = (e: FederatedPointerEvent): void => {
    if (!this.dragging) return
    const dx = e.global.x - this.lastPointerX
    const dy = e.global.y - this.lastPointerY
    this.lastPointerX = e.global.x
    this.lastPointerY = e.global.y
    // a manual drag releases any one-shot focus, but keeps follow off
    this.focusPoint = null
    this.world.position.x += dx
    this.world.position.y += dy
    this.clampPan()
  }

  onPointerUp = (): void => {
    this.dragging = false
  }

  onWheel = (e: FederatedWheelEvent): void => {
    const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1
    this.zoomAt(this.zoom * factor, e.global.x, e.global.y)
  }

  // --- public-ish API used by TownRenderer ---------------------------------

  setZoom(zoom: number): void {
    // zoom toward the centre of the viewport
    this.zoomAt(zoom, this.viewW / 2, this.viewH / 2)
  }

  getZoom(): number {
    return this.zoom
  }

  /** Follow a moving world point; pass null to release. */
  follow(getter: (() => { x: number; y: number }) | null): void {
    this.followTargetGetter = getter
    if (getter) {
      // also do a smooth recentre to wherever the target currently is
      this.focusPoint = getter()
    }
  }

  resize(viewW: number, viewH: number): void {
    this.viewW = viewW
    this.viewH = viewH
    this.clampPan()
  }

  /** Called every frame by the renderer ticker. */
  update(): void {
    if (this.followTargetGetter) {
      const t = this.followTargetGetter()
      this.lerpCenterTo(t.x, t.y)
    } else if (this.focusPoint) {
      const reached = this.lerpCenterTo(this.focusPoint.x, this.focusPoint.y)
      if (reached) this.focusPoint = null
    }
  }

  // --- internals -----------------------------------------------------------

  private zoomAt(nextZoom: number, screenX: number, screenY: number): void {
    const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom))
    if (z === this.zoom) return
    // keep the world point under the cursor fixed during the zoom
    const worldX = (screenX - this.world.position.x) / this.zoom
    const worldY = (screenY - this.world.position.y) / this.zoom
    this.zoom = z
    this.world.scale.set(z)
    this.world.position.x = screenX - worldX * z
    this.world.position.y = screenY - worldY * z
    this.clampPan()
  }

  /** Instantly centre the viewport on a world point. */
  private centerOn(worldX: number, worldY: number, clamp: boolean): void {
    this.world.position.x = this.viewW / 2 - worldX * this.zoom
    this.world.position.y = this.viewH / 2 - worldY * this.zoom
    if (clamp) this.clampPan()
  }

  /** Lerp the viewport centre toward a world point. Returns true once close. */
  private lerpCenterTo(worldX: number, worldY: number): boolean {
    const targetPosX = this.viewW / 2 - worldX * this.zoom
    const targetPosY = this.viewH / 2 - worldY * this.zoom
    const dx = targetPosX - this.world.position.x
    const dy = targetPosY - this.world.position.y
    this.world.position.x += dx * FOLLOW_LERP
    this.world.position.y += dy * FOLLOW_LERP
    this.clampPan()
    return Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5
  }

  /**
   * Clamp panning so the world can't be scrolled far past its edges.
   * If the (scaled) world is smaller than the viewport on an axis it is
   * centred on that axis instead.
   */
  private clampPan(): void {
    const worldW = WORLD_PX_W * this.zoom
    const worldH = WORLD_PX_H * this.zoom

    if (worldW <= this.viewW) {
      this.world.position.x = (this.viewW - worldW) / 2
    } else {
      const minX = this.viewW - worldW
      this.world.position.x = Math.min(0, Math.max(minX, this.world.position.x))
    }

    if (worldH <= this.viewH) {
      this.world.position.y = (this.viewH - worldH) / 2
    } else {
      const minY = this.viewH - worldH
      this.world.position.y = Math.min(0, Math.max(minY, this.world.position.y))
    }
  }
}
