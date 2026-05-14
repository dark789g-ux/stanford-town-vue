import { defineStore } from 'pinia'
import type { Simulation, SimStatus } from '@/types/sim'
import type { AgentFrame, StepFrame, ViewerMode } from '@/types/viewer'
import { SimWsClient } from '@/api/ws'
import { getSim, type StepMovementOut } from '@/api/sims'
import { ApiError } from '@/api/client'

// Base cadence of the playback clock: one step per BASE_TICK_MS at 1x speed.
// At speed S the interval is BASE_TICK_MS / S, so 2x is twice as fast.
const BASE_TICK_MS = 600
const ALLOWED_SPEEDS = [0.5, 1, 2, 4] as const

interface SimSessionState {
  simId: number | null
  sim: Simulation | null
  status: SimStatus
  mode: ViewerMode
  // Only steps that carried a delta have a frame; each frame holds the FULL
  // carried-forward world state as of that step.
  frames: Map<number, StepFrame>
  // Sorted ascending list of the step numbers present in `frames`, so the
  // `currentFrame` getter can resolve any playhead position to "the latest
  // frame at or before currentStep".
  frameSteps: number[]
  currentStep: number
  maxStep: number
  speed: number
  isPlaying: boolean
  connected: boolean
  error: string | null
}

// Non-reactive handles kept outside state so Pinia doesn't proxy them.
let wsClient: SimWsClient | null = null
let clockTimer: number | null = null
// Running per-persona state. The original storage format is delta-encoded —
// a `step` event only carries the personas that *changed* that step (most
// steps carry zero). We accumulate deltas here so every snapshot frame holds
// the FULL world state, and personas don't flicker out between deltas.
let runningAgents: Map<string, AgentFrame> = new Map()

function movementToAgent(m: StepMovementOut): AgentFrame {
  return {
    name: m.persona_name,
    x: m.x,
    y: m.y,
    pronunciatio: m.pronunciatio ?? null,
    description: m.description ?? null,
  }
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return String(e)
}

export const useSimSessionStore = defineStore('simSession', {
  state: (): SimSessionState => ({
    simId: null,
    sim: null,
    status: 'idle',
    mode: 'replay',
    frames: new Map<number, StepFrame>(),
    frameSteps: [],
    currentStep: 0,
    maxStep: 0,
    speed: 1,
    isPlaying: false,
    connected: false,
    error: null,
  }),

  getters: {
    // Resolve the playhead to the latest frame at or before currentStep.
    // Steps without a delta have no frame of their own; they show the
    // carried-forward state from the most recent step that did.
    currentFrame(state): StepFrame | undefined {
      const steps = state.frameSteps
      if (steps.length === 0) return undefined
      // Binary search for the largest step <= currentStep.
      let lo = 0
      let hi = steps.length - 1
      let found = -1
      while (lo <= hi) {
        const mid = (lo + hi) >> 1
        if (steps[mid] <= state.currentStep) {
          found = mid
          lo = mid + 1
        } else {
          hi = mid - 1
        }
      }
      if (found === -1) return state.frames.get(steps[0])
      return state.frames.get(steps[found])
    },
    progress: (state): number =>
      state.maxStep > 0 ? state.currentStep / state.maxStep : 0,
  },

  actions: {
    // --- internal: frame ingestion -----------------------------------------
    _ingestStep(step: number, currTime: string, movements: StepMovementOut[]) {
      // Merge this step's delta into the running per-persona state, then
      // snapshot the FULL state into the frame for `step`.
      for (const m of movements) {
        runningAgents.set(m.persona_name, movementToAgent(m))
      }
      const isNewStep = !this.frames.has(step)
      this.frames.set(step, {
        step,
        curr_time: currTime,
        agents: [...runningAgents.values()],
      })
      // Keep frameSteps sorted. WS replay arrives in order so the common
      // case is a plain append; guard the rare out-of-order arrival.
      if (isNewStep) {
        const steps = this.frameSteps
        if (steps.length === 0 || step > steps[steps.length - 1]) {
          steps.push(step)
        } else {
          steps.push(step)
          steps.sort((a, b) => a - b)
        }
      }
      if (step > this.maxStep) this.maxStep = step
      // In live mode, keep the playhead glued to the newest frame.
      if (this.mode === 'live' && step > this.currentStep) {
        this.currentStep = step
      }
    },

    // --- internal: playback clock ------------------------------------------
    _startClock() {
      this._stopClock()
      const interval = Math.max(50, BASE_TICK_MS / this.speed)
      clockTimer = window.setInterval(() => {
        if (!this.isPlaying) return
        if (this.currentStep < this.maxStep) {
          this.currentStep += 1
        } else if (this.mode === 'replay') {
          // Reached the end of recorded history — stop.
          this.pause()
        }
        // live mode at the head: idle until a new frame bumps maxStep.
      }, interval)
    },

    _stopClock() {
      if (clockTimer != null) {
        window.clearInterval(clockTimer)
        clockTimer = null
      }
    },

    // --- public API ---------------------------------------------------------
    async loadSim(simId: number, mode: ViewerMode): Promise<void> {
      // Tear down any previous session first.
      this.disconnect()

      this.simId = simId
      this.mode = mode
      this.error = null

      try {
        this.sim = await getSim(simId)
        this.status = this.sim.status
      } catch (e) {
        this.error = errMessage(e)
        this.sim = null
      }

      wsClient = new SimWsClient(simId)

      wsClient.onSnapshot((sim, currentStep) => {
        // A snapshot arrives on every (re)subscribe, so it's also our proof the
        // socket is live again after a transient drop + auto-reconnect.
        this.connected = true
        if (sim) {
          this.sim = sim as Simulation
          this.status = (sim as Simulation).status
        }
        if (typeof currentStep === 'number' && currentStep > this.maxStep) {
          this.maxStep = currentStep
        }
      })

      wsClient.onStep((step, currTime, movements) => {
        this._ingestStep(step, currTime, movements as StepMovementOut[])
      })

      wsClient.onStatus((status, errorMessage) => {
        this.status = status as SimStatus
        if (this.sim) this.sim.status = status as SimStatus
        if (errorMessage) this.error = errorMessage
      })

      wsClient.onError((detail) => {
        this.error = detail
      })

      wsClient.onClose((code) => {
        this.connected = false
        if (code === 4404) {
          this.error = `Simulation ${simId} not found`
        }
      })

      // Mirror the WS lifecycle into `connected`. SimWsClient handles its own
      // reconnect/backoff; we optimistically flip connected on subscribe.
      wsClient.connect(0)
      this.connected = true
    },

    play(): void {
      if (this.isPlaying) return
      this.isPlaying = true
      this._startClock()
    },

    pause(): void {
      this.isPlaying = false
      this._stopClock()
    },

    seek(step: number): void {
      const clamped = Math.min(Math.max(0, Math.round(step)), this.maxStep)
      this.currentStep = clamped
    },

    stepForward(): void {
      this.seek(this.currentStep + 1)
    },

    stepBack(): void {
      this.seek(this.currentStep - 1)
    },

    setSpeed(n: number): void {
      const speed = (ALLOWED_SPEEDS as readonly number[]).includes(n) ? n : 1
      this.speed = speed
      // Restart the clock so the new cadence takes effect immediately.
      if (this.isPlaying) this._startClock()
    },

    disconnect(): void {
      this._stopClock()
      if (wsClient) {
        wsClient.disconnect()
        wsClient = null
      }
      runningAgents = new Map()
      this.simId = null
      this.sim = null
      this.status = 'idle'
      this.mode = 'replay'
      this.frames = new Map<number, StepFrame>()
      this.frameSteps = []
      this.currentStep = 0
      this.maxStep = 0
      this.speed = 1
      this.isPlaying = false
      this.connected = false
      this.error = null
    },
  },
})
