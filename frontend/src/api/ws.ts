// Real WebSocket client for live simulation events.
// Protocol mirrors the backend's app/ws/hub.py:
//   client -> {"action":"subscribe","since_step":N}
//   server -> {"event":"snapshot","sim":{...},"current_step":M}
//   server -> {"event":"step","step":K,"curr_time":"...","movements":[...]}  (replay + live)
//   server -> {"event":"status","status":"...","error_message":...}
//   server -> {"event":"llm_call",...} / {"event":"error","detail":"..."}
//   client -> {"action":"ping"} ; server -> {"event":"pong","ts":"..."}
//   client -> {"action":"unsubscribe"}
//   unknown sim_id -> server closes with code 4404.

const HEARTBEAT_INTERVAL_MS = 30_000
const MAX_RECONNECT_ATTEMPTS = 5
const BASE_BACKOFF_MS = 500
const CLOSE_CODE_NOT_FOUND = 4404

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = any

type SnapshotCb = (sim: Json, currentStep: number) => void
type StepCb = (step: number, currTime: string, movements: Json[]) => void
type StatusCb = (status: string, errorMessage: string | null) => void
type ErrorCb = (detail: string) => void
type CloseCb = (code: number) => void

export class SimWsClient {
  private readonly simId: number
  private ws: WebSocket | null = null
  private sinceStep = 0
  private reconnectAttempts = 0
  private heartbeatTimer: number | null = null
  private reconnectTimer: number | null = null
  private manualDisconnect = false

  private snapshotCb: SnapshotCb | null = null
  private stepCb: StepCb | null = null
  private statusCb: StatusCb | null = null
  private errorCb: ErrorCb | null = null
  private closeCb: CloseCb | null = null

  constructor(simId: number) {
    this.simId = simId
  }

  connect(sinceStep: number): void {
    this.sinceStep = sinceStep
    this.manualDisconnect = false
    this.reconnectAttempts = 0
    this.open()
  }

  onSnapshot(cb: SnapshotCb): void {
    this.snapshotCb = cb
  }

  onStep(cb: StepCb): void {
    this.stepCb = cb
  }

  onStatus(cb: StatusCb): void {
    this.statusCb = cb
  }

  onError(cb: ErrorCb): void {
    this.errorCb = cb
  }

  onClose(cb: CloseCb): void {
    this.closeCb = cb
  }

  sendPing(): void {
    this.send({ action: 'ping' })
  }

  disconnect(): void {
    this.manualDisconnect = true
    this.clearTimers()
    if (this.ws) {
      try {
        if (this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ action: 'unsubscribe' }))
        }
        this.ws.close()
      } catch {
        /* ignore */
      }
      this.ws = null
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private send(payload: Record<string, any>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(payload))
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn('[SimWsClient] send failed', err)
      }
    }
  }

  private open(): void {
    const url = `ws://${location.host}/ws/sim/${this.simId}`
    // eslint-disable-next-line no-console
    console.info('[SimWsClient] connecting', url)
    const ws = new WebSocket(url)
    this.ws = ws

    ws.onopen = () => {
      this.reconnectAttempts = 0
      this.send({ action: 'subscribe', since_step: this.sinceStep })
      this.startHeartbeat()
    }

    ws.onmessage = (msg) => {
      let data: Json
      try {
        data = JSON.parse(msg.data as string)
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn('[SimWsClient] bad message', err)
        return
      }
      this.dispatch(data)
    }

    ws.onclose = (ev) => {
      this.stopHeartbeat()
      this.ws = null
      if (this.closeCb) this.closeCb(ev.code)
      if (this.manualDisconnect || ev.code === CLOSE_CODE_NOT_FOUND) return
      this.scheduleReconnect()
    }

    ws.onerror = (err) => {
      // eslint-disable-next-line no-console
      console.warn('[SimWsClient] socket error', err)
    }
  }

  private dispatch(data: Json): void {
    switch (data?.event) {
      case 'snapshot':
        // Track the high-water mark so a reconnect resumes from where we left off.
        if (typeof data.current_step === 'number') {
          this.sinceStep = Math.max(this.sinceStep, data.current_step)
        }
        if (this.snapshotCb) this.snapshotCb(data.sim, data.current_step ?? 0)
        break
      case 'step':
        if (typeof data.step === 'number') {
          this.sinceStep = Math.max(this.sinceStep, data.step)
        }
        if (this.stepCb) {
          this.stepCb(data.step, data.curr_time ?? '', data.movements ?? [])
        }
        break
      case 'status':
        if (this.statusCb) this.statusCb(data.status, data.error_message ?? null)
        break
      case 'error':
        if (this.errorCb) this.errorCb(data.detail ?? 'unknown error')
        break
      case 'pong':
        // heartbeat ack — nothing to do
        break
      default:
        // llm_call and any future events are ignored by this client for M4.
        break
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      // eslint-disable-next-line no-console
      console.error('[SimWsClient] max reconnect attempts reached')
      return
    }
    const delay = BASE_BACKOFF_MS * 2 ** this.reconnectAttempts
    this.reconnectAttempts += 1
    this.reconnectTimer = window.setTimeout(() => this.open(), delay)
  }

  private startHeartbeat(): void {
    this.stopHeartbeat()
    this.heartbeatTimer = window.setInterval(() => this.sendPing(), HEARTBEAT_INTERVAL_MS)
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer != null) {
      window.clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  private clearTimers(): void {
    this.stopHeartbeat()
    if (this.reconnectTimer != null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }
}
