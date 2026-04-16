/**
 * OCPP Load Simulator
 * Симулирует N зарядных станций одновременно подключающихся по OCPP 1.6J WebSocket.
 *
 * Каждая виртуальная станция:
 * 1. Подключается по WS
 * 2. Отправляет BootNotification
 * 3. Отправляет StatusNotification (Available) для 2 коннекторов
 * 4. Периодически шлёт Heartbeat
 * 5. Случайно начинает/завершает зарядки с MeterValues
 *
 * Usage:
 *   STATIONS=10000 WS_URL=ws://localhost:9210/ws tsx src/index.ts
 *   STATIONS=100 CHARGING_PROBABILITY=0.3 tsx src/index.ts
 */

import { WebSocket } from "ws";

// ============================================================================
// CONFIG
// ============================================================================

const config = {
  wsUrl: process.env.WS_URL ?? "ws://localhost:9210/ws",
  totalStations: parseInt(process.env.STATIONS ?? "100"),
  connectBatchSize: parseInt(process.env.BATCH_SIZE ?? "50"),
  connectBatchDelayMs: parseInt(process.env.BATCH_DELAY ?? "200"),
  heartbeatIntervalMs: parseInt(process.env.HEARTBEAT_INTERVAL ?? "30000"),
  meterValuesIntervalMs: parseInt(process.env.METER_INTERVAL ?? "10000"),
  chargingProbability: parseFloat(process.env.CHARGING_PROBABILITY ?? "0.1"),
  connectorsPerStation: parseInt(process.env.CONNECTORS ?? "2"),
  stationPrefix: process.env.STATION_PREFIX ?? "LOAD-SIM",
  verbose: process.env.VERBOSE === "true",
};

// ============================================================================
// STATS
// ============================================================================

const stats = {
  connected: 0,
  disconnected: 0,
  errors: 0,
  messagesSent: 0,
  messagesReceived: 0,
  bootAcks: 0,
  chargingActive: 0,
  startTime: Date.now(),
};

// ============================================================================
// OCPP MESSAGE HELPERS
// ============================================================================

let msgIdCounter = 0;
function nextMsgId(): string {
  return `msg-${++msgIdCounter}`;
}

function ocppCall(action: string, payload: Record<string, unknown>): string {
  return JSON.stringify([2, nextMsgId(), action, payload]);
}

// ============================================================================
// VIRTUAL STATION
// ============================================================================

interface ConnectorState {
  status: "Available" | "Charging" | "Finishing";
  transactionId: number;
  meterStart: number;
  meterCurrent: number;
  idTag: string;
}

class VirtualStation {
  id: string;
  ws?: WebSocket;
  connectors: ConnectorState[] = [];
  heartbeatTimer?: ReturnType<typeof setInterval>;
  meterTimer?: ReturnType<typeof setInterval>;
  chargingTimer?: ReturnType<typeof setTimeout>;
  connected = false;

  constructor(index: number) {
    this.id = `${config.stationPrefix}-${String(index).padStart(5, "0")}`;
    for (let i = 0; i < config.connectorsPerStation; i++) {
      this.connectors.push({
        status: "Available",
        transactionId: 0,
        meterStart: 0,
        meterCurrent: 0,
        idTag: "",
      });
    }
  }

  async connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = `${config.wsUrl}/${this.id}`;
      this.ws = new WebSocket(url, ["ocpp1.6"], {
        rejectUnauthorized: false,
        handshakeTimeout: 10000,
      });

      const timeout = setTimeout(() => {
        this.ws?.close();
        reject(new Error(`Timeout connecting ${this.id}`));
      }, 15000);

      this.ws.on("open", () => {
        clearTimeout(timeout);
        this.connected = true;
        stats.connected++;
        this.onConnected();
        resolve();
      });

      this.ws.on("message", (data) => {
        stats.messagesReceived++;
        this.onMessage(data.toString());
      });

      this.ws.on("close", () => {
        clearTimeout(timeout);
        if (this.connected) {
          this.connected = false;
          stats.connected--;
          stats.disconnected++;
        }
        this.cleanup();
      });

      this.ws.on("error", (err) => {
        clearTimeout(timeout);
        stats.errors++;
        if (config.verbose) console.error(`[${this.id}] WS error: ${err.message}`);
      });
    });
  }

  private send(msg: string) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(msg);
      stats.messagesSent++;
    }
  }

  private onConnected() {
    // BootNotification
    this.send(
      ocppCall("BootNotification", {
        chargePointVendor: "LoadSimulator",
        chargePointModel: "VirtualStation",
        chargePointSerialNumber: this.id,
        firmwareVersion: "1.0.0-sim",
      })
    );

    // StatusNotification for each connector
    for (let i = 0; i < this.connectors.length; i++) {
      this.send(
        ocppCall("StatusNotification", {
          connectorId: i + 1,
          errorCode: "NoError",
          status: "Available",
        })
      );
    }

    // Start heartbeat
    this.heartbeatTimer = setInterval(() => {
      this.send(ocppCall("Heartbeat", {}));
    }, config.heartbeatIntervalMs + Math.random() * 5000);

    // Random charging activity
    this.scheduleChargingActivity();
  }

  private onMessage(raw: string) {
    try {
      const data = JSON.parse(raw);
      const [type, messageId, ...rest] = data;

      if (type === 2) {
        // Server request (RemoteStartTransaction, RemoteStopTransaction, etc.)
        const [action, payload] = rest;
        this.handleServerRequest(messageId, action, payload);
      } else if (type === 3) {
        // CallResult (response to our request)
        const [payload] = rest;
        if (payload?.status === "Accepted") stats.bootAcks++;
      }
    } catch {
      // ignore parse errors
    }
  }

  private handleServerRequest(messageId: string, action: string, payload: Record<string, unknown>) {
    // Respond to server requests
    switch (action) {
      case "RemoteStartTransaction": {
        const connId = (payload.connectorId as number) ?? 1;
        const idTag = (payload.idTag as string) ?? "REMOTE";
        this.startCharging(connId - 1, idTag);
        this.ws?.send(JSON.stringify([3, messageId, { status: "Accepted" }]));
        break;
      }
      case "RemoteStopTransaction": {
        const txId = payload.transactionId as number;
        const connIdx = this.connectors.findIndex((c) => c.transactionId === txId);
        if (connIdx >= 0) this.stopCharging(connIdx);
        this.ws?.send(JSON.stringify([3, messageId, { status: "Accepted" }]));
        break;
      }
      case "Reset":
        this.ws?.send(JSON.stringify([3, messageId, { status: "Accepted" }]));
        break;
      case "GetConfiguration":
        this.ws?.send(JSON.stringify([3, messageId, { configurationKey: [], unknownKey: [] }]));
        break;
      default:
        // Generic accept
        this.ws?.send(JSON.stringify([3, messageId, {}]));
    }
  }

  private scheduleChargingActivity() {
    const delay = 10000 + Math.random() * 60000; // 10-70 seconds
    this.chargingTimer = setTimeout(() => {
      if (!this.connected) return;

      // Pick random available connector
      const availableIdx = this.connectors.findIndex((c) => c.status === "Available");
      if (availableIdx >= 0 && Math.random() < config.chargingProbability) {
        this.startCharging(availableIdx, `TAG-${Math.floor(Math.random() * 10000)}`);
      }

      // Maybe stop a charging connector
      const chargingIdx = this.connectors.findIndex((c) => c.status === "Charging");
      if (chargingIdx >= 0 && Math.random() < 0.3) {
        this.stopCharging(chargingIdx);
      }

      this.scheduleChargingActivity();
    }, delay);
  }

  private startCharging(connIdx: number, idTag: string) {
    const conn = this.connectors[connIdx];
    if (!conn || conn.status !== "Available") return;

    const txId = Math.floor(Math.random() * 1000000);
    conn.status = "Charging";
    conn.transactionId = txId;
    conn.meterStart = Math.floor(Math.random() * 100000);
    conn.meterCurrent = conn.meterStart;
    conn.idTag = idTag;
    stats.chargingActive++;

    // Authorize
    this.send(ocppCall("Authorize", { idTag }));

    // StartTransaction
    this.send(
      ocppCall("StartTransaction", {
        connectorId: connIdx + 1,
        idTag,
        meterStart: conn.meterStart,
        timestamp: new Date().toISOString(),
      })
    );

    // StatusNotification: Charging
    this.send(
      ocppCall("StatusNotification", {
        connectorId: connIdx + 1,
        errorCode: "NoError",
        status: "Charging",
      })
    );

    // Start MeterValues reporting
    if (!this.meterTimer) {
      this.meterTimer = setInterval(() => this.sendMeterValues(), config.meterValuesIntervalMs);
    }
  }

  private stopCharging(connIdx: number) {
    const conn = this.connectors[connIdx];
    if (!conn || conn.status !== "Charging") return;

    stats.chargingActive--;

    // StopTransaction
    this.send(
      ocppCall("StopTransaction", {
        transactionId: conn.transactionId,
        meterStop: conn.meterCurrent,
        timestamp: new Date().toISOString(),
        reason: "Local",
      })
    );

    // StatusNotification: Available
    this.send(
      ocppCall("StatusNotification", {
        connectorId: connIdx + 1,
        errorCode: "NoError",
        status: "Available",
      })
    );

    conn.status = "Available";
    conn.transactionId = 0;

    // Stop meter timer if no more charging connectors
    if (!this.connectors.some((c) => c.status === "Charging") && this.meterTimer) {
      clearInterval(this.meterTimer);
      this.meterTimer = undefined;
    }
  }

  private sendMeterValues() {
    for (let i = 0; i < this.connectors.length; i++) {
      const conn = this.connectors[i];
      if (conn.status !== "Charging") continue;

      // Increment meter (7-50 kW charging, ~0.1-0.5 kWh per 10s interval)
      const increment = Math.floor(100 + Math.random() * 500); // Wh
      conn.meterCurrent += increment;

      this.send(
        ocppCall("MeterValues", {
          connectorId: i + 1,
          transactionId: conn.transactionId,
          meterValue: [
            {
              timestamp: new Date().toISOString(),
              sampledValue: [
                {
                  value: String(conn.meterCurrent),
                  context: "Sample.Periodic",
                  measurand: "Energy.Active.Import.Register",
                  unit: "Wh",
                },
                {
                  value: String(7000 + Math.floor(Math.random() * 43000)),
                  context: "Sample.Periodic",
                  measurand: "Power.Active.Import",
                  unit: "W",
                },
              ],
            },
          ],
        })
      );
    }
  }

  cleanup() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    if (this.meterTimer) clearInterval(this.meterTimer);
    if (this.chargingTimer) clearTimeout(this.chargingTimer);
  }

  disconnect() {
    this.cleanup();
    this.ws?.close();
  }
}

// ============================================================================
// ORCHESTRATOR
// ============================================================================

async function run() {
  console.log(`\n🔌 OCPP Load Simulator`);
  console.log(`══════════════════════════════════════════`);
  console.log(`  Target:        ${config.wsUrl}`);
  console.log(`  Stations:      ${config.totalStations.toLocaleString()}`);
  console.log(`  Connectors:    ${config.connectorsPerStation} per station`);
  console.log(`  Batch size:    ${config.connectBatchSize}`);
  console.log(`  Batch delay:   ${config.connectBatchDelayMs}ms`);
  console.log(`  Heartbeat:     ${config.heartbeatIntervalMs / 1000}s`);
  console.log(`  Charging prob: ${(config.chargingProbability * 100).toFixed(0)}%`);
  console.log(`══════════════════════════════════════════\n`);

  const stations: VirtualStation[] = [];

  // Connect in batches
  const totalBatches = Math.ceil(config.totalStations / config.connectBatchSize);

  for (let batch = 0; batch < totalBatches; batch++) {
    const start = batch * config.connectBatchSize;
    const end = Math.min(start + config.connectBatchSize, config.totalStations);
    const batchStations: VirtualStation[] = [];

    for (let i = start; i < end; i++) {
      batchStations.push(new VirtualStation(i));
    }

    // Connect batch in parallel
    const results = await Promise.allSettled(batchStations.map((s) => s.connect()));

    let batchOk = 0;
    let batchFail = 0;
    results.forEach((r, idx) => {
      if (r.status === "fulfilled") {
        batchOk++;
        stations.push(batchStations[idx]);
      } else {
        batchFail++;
        stats.errors++;
      }
    });

    const elapsed = ((Date.now() - stats.startTime) / 1000).toFixed(1);
    const pct = (((batch + 1) / totalBatches) * 100).toFixed(0);
    process.stdout.write(
      `\r  [${pct}%] Connected: ${stats.connected.toLocaleString()} / ${config.totalStations.toLocaleString()} | ` +
        `Errors: ${stats.errors} | Charging: ${stats.chargingActive} | ${elapsed}s`
    );

    if (batch < totalBatches - 1) {
      await new Promise((r) => setTimeout(r, config.connectBatchDelayMs));
    }
  }

  console.log(`\n\n✅ Connection phase complete!`);
  console.log(`  Connected: ${stats.connected.toLocaleString()}`);
  console.log(`  Failed:    ${stats.errors}`);
  console.log(`  Time:      ${((Date.now() - stats.startTime) / 1000).toFixed(1)}s\n`);

  // Stats reporter
  const statsInterval = setInterval(() => {
    const uptime = ((Date.now() - stats.startTime) / 1000).toFixed(0);
    const msgRate = (stats.messagesSent / (Date.now() - stats.startTime) * 1000).toFixed(0);
    console.log(
      `  [${uptime}s] ` +
        `Connected: ${stats.connected.toLocaleString()} | ` +
        `Charging: ${stats.chargingActive} | ` +
        `Sent: ${stats.messagesSent.toLocaleString()} | ` +
        `Recv: ${stats.messagesReceived.toLocaleString()} | ` +
        `Rate: ${msgRate} msg/s | ` +
        `Errors: ${stats.errors}`
    );
  }, 10000);

  // Graceful shutdown
  const shutdown = () => {
    console.log(`\n\n🛑 Shutting down ${stations.length} stations...`);
    clearInterval(statsInterval);
    stations.forEach((s) => s.disconnect());
    setTimeout(() => {
      console.log(`\n📊 Final stats:`);
      console.log(`  Total connected:    ${stats.connected.toLocaleString()}`);
      console.log(`  Messages sent:      ${stats.messagesSent.toLocaleString()}`);
      console.log(`  Messages received:  ${stats.messagesReceived.toLocaleString()}`);
      console.log(`  Total errors:       ${stats.errors}`);
      console.log(`  Runtime:            ${((Date.now() - stats.startTime) / 1000).toFixed(1)}s`);
      process.exit(0);
    }, 2000);
  };

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

run().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
