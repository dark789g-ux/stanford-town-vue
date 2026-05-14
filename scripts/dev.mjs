// 根目录 dev 编排：先启动后端，待 /api/health 健康后再启动前端。
// 设计见 docs/superpowers/specs/2026-05-14-root-pnpm-dev-orchestration-design.md
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import net from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const BACKEND_DIR = join(ROOT, "backend");
const FRONTEND_DIR = join(ROOT, "frontend");
const VITE_BIN = join(FRONTEND_DIR, "node_modules", "vite", "bin", "vite.js");

const HEALTH_URL = "http://localhost:8000/api/health";
const HEALTH_PORT = Number(new URL(HEALTH_URL).port || 80);
const HEALTH_INTERVAL_MS = 1000;
const HEALTH_TIMEOUT_MS = 60_000;

const isWindows = process.platform === "win32";

// --- 彩色前缀输出 -------------------------------------------------
const COLORS = { backend: "\x1b[36m", frontend: "\x1b[35m", dev: "\x1b[32m" };
const RESET = "\x1b[0m";

function log(tag, line) {
  process.stdout.write(`${COLORS[tag] ?? ""}[${tag}]${RESET} ${line}\n`);
}

// 把子进程输出逐行加前缀转发
function pipeWithPrefix(child, tag) {
  for (const stream of [child.stdout, child.stderr]) {
    if (!stream) continue;
    let buf = "";
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => {
      buf += chunk;
      const lines = buf.split(/\r?\n/);
      buf = lines.pop() ?? "";
      for (const l of lines) log(tag, l);
    });
    stream.on("end", () => {
      if (buf.length) log(tag, buf);
    });
  }
}

// --- 进程管理 -----------------------------------------------------
/** @type {{ name: string, child: import("node:child_process").ChildProcess }[]} */
const procs = [];
let shuttingDown = false;

// 不用 shell:true：直接拿到真实进程的 pid，taskkill /T 才能可靠清掉整棵子树
function spawnProc(name, command, args, cwd) {
  const child = spawn(command, args, { cwd });
  procs.push({ name, child });
  pipeWithPrefix(child, name);

  child.on("error", (err) => {
    log("dev", `${name} 启动出错：${err.message}`);
    shutdown(1);
  });

  child.on("exit", (code, signal) => {
    if (shuttingDown) return;
    log("dev", `${name} 进程退出（code=${code}, signal=${signal}），正在停止其它进程…`);
    shutdown(code ?? 1);
  });

  return child;
}

function killProc({ child }) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  if (isWindows && child.pid) {
    // 同步等待 taskkill 完成；/T 杀掉整棵进程树（如 uvicorn.exe -> python.exe）
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const p of procs) killProc(p);
  process.exit(code);
}

process.on("SIGINT", () => {
  log("dev", "收到 Ctrl+C，正在停止后端与前端…");
  shutdown(0);
});

// --- 健康轮询 -----------------------------------------------------
async function waitForBackend() {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (shuttingDown) return false;
    try {
      const res = await fetch(HEALTH_URL);
      if (res.ok) return true;
    } catch {
      // 后端尚未就绪，继续等待
    }
    await new Promise((r) => setTimeout(r, HEALTH_INTERVAL_MS));
  }
  return false;
}

// --- 主流程 -------------------------------------------------------
// 启动前探测端口：8000 被占时 uvicorn 会「先跑 lifespan startup，再 bind 失败」，
// 然后立刻 shutdown 并以 code=1 退出——报错很隐蔽。提前检测可给出可操作的提示。
function checkPortFree(port) {
  return new Promise((resolve) => {
    const tester = net.createServer();
    tester.once("error", (err) => resolve(err.code !== "EADDRINUSE"));
    tester.once("listening", () => tester.close(() => resolve(true)));
    tester.listen(port, "127.0.0.1");
  });
}

function resolveExecutable(name) {
  const finder = isWindows ? "where" : "which";
  const res = spawnSync(finder, [name], { encoding: "utf8" });
  if (res.status !== 0 || !res.stdout) return null;
  return res.stdout.split(/\r?\n/)[0].trim() || null;
}

async function main() {
  const uvicorn = resolveExecutable("uvicorn");
  if (!uvicorn) {
    log("dev", "找不到 uvicorn：请确认后端依赖已安装并在 PATH 上（参见 README 的后端安装一节）。");
    process.exit(1);
  }
  if (!existsSync(VITE_BIN)) {
    log("dev", `找不到前端依赖（缺 ${VITE_BIN}）：请先在 frontend 目录运行 pnpm install。`);
    process.exit(1);
  }

  if (!(await checkPortFree(HEALTH_PORT))) {
    log("dev", `端口 ${HEALTH_PORT} 已被占用——多半是上一次没正常关闭（直接关终端而非 Ctrl+C）残留的后端进程。`);
    log("dev", "请先释放该端口再重试：");
    if (isWindows) {
      log("dev", `  Get-Process -Id (Get-NetTCPConnection -LocalPort ${HEALTH_PORT} -State Listen).OwningProcess`);
      log("dev", "  taskkill /pid <PID> /T /F");
    } else {
      log("dev", `  lsof -i :${HEALTH_PORT}    然后  kill <PID>`);
    }
    process.exit(1);
  }

  log("dev", "启动后端：uvicorn app.main:app");
  spawnProc("backend", uvicorn, ["app.main:app"], BACKEND_DIR);

  log("dev", `等待后端就绪（轮询 ${HEALTH_URL}，最多 ${HEALTH_TIMEOUT_MS / 1000}s）…`);
  const ready = await waitForBackend();
  if (shuttingDown) return;
  if (!ready) {
    log("dev", `后端在 ${HEALTH_TIMEOUT_MS / 1000}s 内未就绪，放弃启动。`);
    shutdown(1);
    return;
  }

  log("dev", "后端已就绪，启动前端：vite");
  spawnProc("frontend", process.execPath, [VITE_BIN], FRONTEND_DIR);
}

main();
