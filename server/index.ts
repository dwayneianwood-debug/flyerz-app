import express, { type Request, Response, NextFunction } from "express";
import { registerRoutes } from "./routes";
import { serveStatic } from "./static";
import { createServer } from "http";
import os from "os";
import path from "path";
import fs from "fs";
import { execSync } from "child_process";

/** Sync load project `.env` before any env-dependent constants (no dotenv dependency). */
(() => {
  try {
    const envPath = path.join(process.cwd(), ".env");
    if (!fs.existsSync(envPath)) return;
    const raw = fs.readFileSync(envPath, "utf8");
    for (const line of raw.split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const eq = t.indexOf("=");
      if (eq <= 0) continue;
      const k = t.slice(0, eq).trim();
      let v = t.slice(eq + 1).trim();
      if (
        (v.startsWith('"') && v.endsWith('"')) ||
        (v.startsWith("'") && v.endsWith("'"))
      ) {
        v = v.slice(1, -1);
      }
      if (process.env[k] === undefined) process.env[k] = v;
    }
  } catch {
    /* ignore malformed .env */
  }
})();

let sighupCount = 0;
const PYTHON_BIN = process.env.PYTHON_BIN || (process.platform === "win32" ? "python" : "python3");
const SKIP_GATEKEEPER = process.env.SKIP_GATEKEEPER === "1";
process.on("SIGHUP", () => {
  sighupCount++;
  if (sighupCount > 1) {
    process.exit(0);
  }
});

let gatekeeperPassed = false;

const app = express();
const httpServer = createServer(app);

/** Strip IPv4-mapped IPv6 prefix for range checks. */
function flyerzClientIpv4(raw: string): string | null {
  if (!raw) return null;
  const trimmed = raw.startsWith("::ffff:") ? raw.slice(7) : raw;
  if (trimmed.includes(":")) {
    return trimmed === "::1" ? "127.0.0.1" : null;
  }
  return trimmed;
}

function flyerzIsAllowedLanOrLocalhost(ipv4OrNormalized: string): boolean {
  const parts = ipv4OrNormalized.split(".").map((p) => parseInt(p, 10));
  if (parts.length !== 4 || parts.some((n) => Number.isNaN(n) || n < 0 || n > 255)) {
    return false;
  }
  const [a, b] = parts;
  if (a === 127) return true;
  if (a === 10) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  return false;
}

/** LAN-only gate: when LAN_ONLY_MODE=true, allow localhost + RFC1918 only (blocks WAN). No-op otherwise. */
function flyerzLanOnlyBouncer(req: Request, res: Response, next: NextFunction) {
  if (process.env.LAN_ONLY_MODE !== "true") {
    return next();
  }
  const raw =
    req.socket.remoteAddress ??
    (req as Request & { connection?: { remoteAddress?: string } }).connection
      ?.remoteAddress ??
    req.ip ??
    "";
  const v4 = flyerzClientIpv4(raw);
  if (!v4 || !flyerzIsAllowedLanOrLocalhost(v4)) {
    return res.status(403).send("Access Denied: Flyerz Internal Network Only");
  }
  next();
}

app.use(flyerzLanOnlyBouncer);

declare module "http" {
  interface IncomingMessage {
    rawBody: unknown;
  }
}

app.get('/health', (_req, res) => res.status(200).send('OK'));

app.use((req, res, next) => {
  if (!gatekeeperPassed && req.path !== '/health') {
    return res.status(503).json({ message: "Server starting — gatekeeper checks in progress" });
  }
  next();
});

app.use(
  express.json({
    verify: (req, _res, buf) => {
      req.rawBody = buf;
    },
  }),
);

app.use(express.urlencoded({ extended: false }));

/** Prefer office LAN IPs over VPN/tunnel adapters when printing the team share link. */
function flyerzFirstNonInternalIpv4(): string | null {
  const nets = os.networkInterfaces();
  if (!nets) return null;

  type Cand = { address: string; name: string; cidr: string | null };
  const cands: Cand[] = [];
  for (const [name, iface] of Object.entries(nets)) {
    if (!iface) continue;
    for (const addr of iface) {
      if (addr.internal) continue;
      const fam = addr.family as string | number;
      if (fam !== "IPv4" && fam !== 4) continue;
      cands.push({
        address: addr.address,
        name,
        cidr: addr.cidr ?? null,
      });
    }
  }
  if (cands.length === 0) return null;

  const isVpnish = (c: Cand) =>
    /vpn|tun|tap|proton|wireguard|zerotier|hamachi|nord|cisco|anyconnect|globalprotect|forticlient|protun/i.test(
      c.name,
    ) ||
    // Point-to-point /32 tunnels are almost never the office LAN share target.
    (typeof c.cidr === "string" && c.cidr.endsWith("/32"));

  const score = (c: Cand): number => {
    if (isVpnish(c)) return 0;
    if (c.address.startsWith("192.168.")) return 100;
    if (/^172\.(1[6-9]|2\d|3[0-1])\./.test(c.address)) return 90;
    if (c.address.startsWith("10.")) return 80;
    return 10;
  };

  cands.sort((a, b) => score(b) - score(a));
  return cands[0]?.address ?? null;
}

function flyerzResolveListenHost(): string {
  const explicit = process.env.HOST?.trim();
  if (explicit) return explicit;
  // LAN office sharing: accept connections from the network; LAN_ONLY_MODE still blocks WAN.
  if (process.env.LAN_ONLY_MODE === "true") return "0.0.0.0";
  return "127.0.0.1";
}

function flyerzPrintTeamSharingBanner(port: number, listenHost: string): void {
  const lanIp = flyerzFirstNonInternalIpv4();
  const pcName = os.hostname();
  const ipLink = `http://${lanIp ?? "127.0.0.1"}:${port}`;
  const nameLink = `http://${pcName}:${port}`;
  const lanOnly = process.env.LAN_ONLY_MODE === "true";
  const networkReachable = listenHost === "0.0.0.0" || listenHost === "::";

  console.log("");
  console.log("=========================================");
  if (lanOnly) {
    console.log("FLYERZ LAN AIRLOCK MODE ACTIVE");
  } else if (networkReachable) {
    console.log("FLYERZ NETWORK LISTEN (no LAN_ONLY gate)");
  } else {
    console.log("FLYERZ LOCAL-ONLY (localhost)");
  }
  console.log(`Listen: ${listenHost}:${port}`);
  if (networkReachable) {
    console.log(`TEAM LINK (IP):   ${ipLink}`);
    console.log(`TEAM LINK (name): ${nameLink}`);
    console.log("Share the IP link if the PC name does not resolve on your network.");
    console.log("Allow TCP port in Windows Firewall if colleagues cannot connect.");
  } else {
    console.log(`Local only: http://127.0.0.1:${port}`);
    console.log("Set LAN_ONLY_MODE=true (and restart) to share on the company network.");
  }
  console.log("=========================================");
  if (networkReachable && !lanIp) {
    console.log(
      "[flyerz] No non-internal IPv4 yet — connect to company Wi-Fi/Ethernet, then restart.\n",
    );
  } else {
    console.log("");
  }
}

export function log(message: string, source = "express") {
  const formattedTime = new Date().toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });

  console.log(`${formattedTime} [${source}] ${message}`);
}

app.use((req, res, next) => {
  const start = Date.now();
  const path = req.path;
  let capturedJsonResponse: Record<string, any> | undefined = undefined;

  const originalResJson = res.json;
  res.json = function (bodyJson, ...args) {
    capturedJsonResponse = bodyJson;
    return originalResJson.apply(res, [bodyJson, ...args]);
  };

  res.on("finish", () => {
    const duration = Date.now() - start;
    if (path.startsWith("/api")) {
      let logLine = `${req.method} ${path} ${res.statusCode} in ${duration}ms`;
      if (capturedJsonResponse) {
        try {
          const isLarge = Array.isArray(capturedJsonResponse)
            ? capturedJsonResponse.length > 5
            : false;
          if (isLarge) {
            logLine += ` :: [${capturedJsonResponse.length} items]`;
          } else {
            const str = JSON.stringify(capturedJsonResponse);
            logLine += ` :: ${str.length > 200 ? str.slice(0, 200) + "..." : str}`;
          }
        } catch {
          logLine += ` :: [response logged]`;
        }
      }

      log(logLine);
    }
  });

  next();
});

const port = parseInt(process.env.PORT || "5000", 10);
const listenHost = flyerzResolveListenHost();

(async () => {
  await registerRoutes(httpServer, app);

  app.use((err: any, _req: Request, res: Response, next: NextFunction) => {
    const status = err.status || err.statusCode || 500;
    const message = err.message || "Internal Server Error";

    console.error("Internal Server Error:", err);

    if (res.headersSent) {
      return next(err);
    }

    return res.status(status).json({ message });
  });

  if (process.env.NODE_ENV === "production") {
    serveStatic(app);
  } else {
    const { setupVite } = await import("./vite");
    await setupVite(httpServer, app);
  }

  httpServer.listen(
    {
      port,
      host: listenHost,
    },
    () => {
      log(`Server listening on ${listenHost}:${port}`);
      flyerzPrintTeamSharingBanner(port, listenHost);

      const gatekeeperScript = path.join(process.cwd(), "server", "pre_deploy_check.py");

      if (!fs.existsSync(gatekeeperScript)) {
        console.error(
          "\n╔══════════════════════════════════════════════════════════╗"
        );
        console.error(
          "║  SECURITY ERROR: GATEKEEPER SCRIPT NOT FOUND           ║"
        );
        console.error(
          "║  APPLICATION WILL NOT START WITHOUT V1.0 HARD GATE     ║"
        );
        console.error(
          "╚══════════════════════════════════════════════════════════╝\n"
        );
        console.error(`Expected path: ${gatekeeperScript}`);
        console.error(`Working directory: ${process.cwd()}`);
        process.exit(1);
      }

      if (SKIP_GATEKEEPER) {
        console.log("[GATEKEEPER] Skipped because SKIP_GATEKEEPER=1.");
        gatekeeperPassed = true;
        log("app fully initialized");
        return;
      }

      try {
        console.log("[GATEKEEPER] Running Flyerz V1.0 pre-deploy checks...");
        execSync(`${PYTHON_BIN} "${gatekeeperScript}"`, {
          stdio: "inherit",
          timeout: 600_000,
          env: {
            ...process.env,
            PYTHONIOENCODING: "utf-8",
            PYTHONUTF8: "1",
          },
        });
        console.log("[GATEKEEPER] All gates passed — application cleared to start.");
        gatekeeperPassed = true;
        log("app fully initialized");
      } catch (err: any) {
        console.error(
          "\n╔══════════════════════════════════════════════════════════╗"
        );
        console.error(
          "║  RULE VIOLATION DETECTED — APPLICATION WILL NOT START   ║"
        );
        console.error(
          "╚══════════════════════════════════════════════════════════╝\n"
        );
        console.error(
          "Fix all violations in server/pre_deploy_check.py output above."
        );
        process.exit(1);
      }
    },
  );
})();
