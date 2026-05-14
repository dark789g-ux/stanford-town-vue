// Dev harness — NOT wired into the app. Documents how the Vue layer should
// drive the TownRenderer. Safe to import or copy-paste while prototyping; it
// does nothing unless demoTownRenderer() is explicitly called.

import { TownRenderer, type StepFrame } from './index'

/**
 * Minimal end-to-end usage example.
 *
 * In the real app, the Vue component:
 *   1. owns a <canvas> ref,
 *   2. constructs a TownRenderer with it,
 *   3. awaits load(),
 *   4. calls setStep() once per simulation step (driven by the Pinia store's
 *      playback clock),
 *   5. wires zoom/focus controls to setCameraZoom()/focusOnAgent(),
 *   6. calls resize() on container resize and destroy() on unmount.
 */
export async function demoTownRenderer(canvas: HTMLCanvasElement): Promise<TownRenderer> {
  const renderer = new TownRenderer({
    canvas,
    assetBaseUrl: '/assets', // Vite proxies /assets/* to the FastAPI backend
    onAgentClick: (name) => {
      // The Vue layer would open this persona's state card here.
      console.log('agent clicked:', name)
    },
  })

  // Loads ground + foreground + atlas.json + all 25 character sheets.
  await renderer.load()
  console.log('renderer.ready =', renderer.ready)

  // A hard-coded first step. In production these come from the backend.
  const step0: StepFrame = {
    step: 0,
    curr_time: '2023-02-13T07:00:00',
    agents: [
      {
        name: 'Isabella Rodriguez',
        x: 72,
        y: 14,
        pronunciatio: '\u{1F634}', // sleeping emoji
        description: 'sleeping in bed',
      },
      {
        name: 'Klaus Mueller',
        x: 126,
        y: 46,
        pronunciatio: '☕', // hot beverage
        description: 'making coffee',
      },
      {
        name: 'Maria Lopez',
        x: 123,
        y: 57,
        pronunciatio: null,
        description: 'waking up',
      },
    ],
  }
  renderer.setStep(step0)

  // A second step — agents that moved are tweened smoothly toward the new
  // tiles; agents absent from the frame would be removed.
  const step1: StepFrame = {
    step: 1,
    curr_time: '2023-02-13T07:00:10',
    agents: [
      { name: 'Isabella Rodriguez', x: 73, y: 14, pronunciatio: '\u{1F6B6}', description: 'walking' },
      { name: 'Klaus Mueller', x: 126, y: 46, pronunciatio: '☕', description: 'drinking coffee' },
      { name: 'Maria Lopez', x: 123, y: 58, pronunciatio: '\u{1F6B6}', description: 'walking to desk' },
    ],
  }
  // Typically called ~once per playback tick:
  renderer.setStep(step1)

  // Camera controls.
  renderer.setCameraZoom(1.5)
  console.log('zoom =', renderer.getCameraZoom())
  renderer.focusOnAgent('Isabella Rodriguez') // follow her
  renderer.focusOnAgent(null) // release the camera

  // On unmount the Vue component must call:
  //   renderer.destroy()

  return renderer
}
