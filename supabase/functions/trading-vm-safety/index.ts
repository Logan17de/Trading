// Independent cloud VM-outage watchdog. Scheduled via authorized Supabase Cron.
// No broker credentials or trading permissions. Request body is ignored.
// verify_jwt=true: only an authenticated scheduler can invoke it.

type Claim = {
  status: string;
  token?: string;
  risk_state?: "ACTIVE" | "FLAT" | "UNKNOWN";
  outage_detected_at?: string;
};

async function rpc(base: string, key: string, name: string, body: object): Promise<Claim> {
  const headers: Record<string, string> = {
    "apikey": key,
    "Content-Type": "application/json",
  };
  // Legacy service-role JWT needs bearer; newer Supabase secret keys use apikey.
  if (key.split(".").length === 3) headers.Authorization = "Bearer " + key;
  const result = await fetch(base + "/rest/v1/rpc/" + name, {
    method: "POST", headers, body: JSON.stringify(body),
    signal: AbortSignal.timeout(8000),
  });
  if (!result.ok) throw new Error("PRIVATE_RPC_UNAVAILABLE");
  return await result.json() as Claim;
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("Method not allowed", { status: 405 });
  const base = Deno.env.get("SUPABASE_URL") || "";
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  const mailKey = Deno.env.get("TRADING_SAFETY_RESEND_KEY") || "";
  const sender = Deno.env.get("TRADING_SAFETY_FROM") || "";
  const recipient = Deno.env.get("TRADING_SAFETY_TO") || "";
  if (!base.startsWith("https://") || !key || !mailKey || !sender || !recipient) {
    return Response.json({ status: "ALERT_TRANSPORT_UNCONFIGURED", email_sent: false }, {status: 503});
  }
  try {
    const claimed = await rpc(base, key, "claim_trading_vm_outage", {});
    if (claimed.status !== "SEND") {
      return Response.json({ status: claimed.status, email_sent: false });
    }
    const token = claimed.token || "";
    const risk = claimed.risk_state;
    if (!/^[a-f0-9-]{36}$/.test(token) ||
        !["ACTIVE","FLAT","UNKNOWN"].includes(risk || "")) {
      throw new Error("INVALID_PRIVATE_CLAIM");
    }
    // No untrusted free-text or account data may appear in alert body.
    const subject = "Options Trader · Oracle VM unreachable · " + risk;
    const payload = {
      from: sender,
      to: [recipient],
      subject,
      html: "<h2>Oracle VM / heartbeat unavailable</h2>" +
        "<p>Last confirmed exposure: <b>" + risk + "</b>.</p>" +
        "<p>The last heartbeat is over two minutes old. " +
        "This does not prove that the broker is flat or that stops filled. " +
        "Inspect open orders, positions and SL/GTT directly in Groww.</p>" +
        "<p>Automatic orders, retries and repairs are disabled in this monitor.</p>",
    };
    const response = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + mailKey,
        "Content-Type": "application/json",
        "Idempotency-Key": "trading-safety-vm-" + token,
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(12000),
    });
    if (!response.ok) return Response.json(
      { status: "PROVIDER_NOT_ACCEPTED", email_sent: false }, { status: 502 });
    const receipt: unknown = await response.json();
    if (typeof receipt !== "object" || receipt === null ||
        !("id" in receipt) || typeof receipt.id !== "string" || !receipt.id) {
      throw new Error("PROVIDER_RECEIPT_REQUIRED");
    }
    const ack = await rpc(base, key, "ack_trading_vm_outage", { token });
    if (!("acknowledged" in ack) || ack.acknowledged !== true) {
      // Duplicate retry retains original provider idempotency key.
      throw new Error("DURABLE_ACK_NOT_VERIFIED");
    }
    return Response.json({ status: "PROVIDER_ACCEPTED", email_sent: true,
                           inbox_verified: false });
  } catch {
    return Response.json({ status: "MONITOR_UNAVAILABLE", email_sent: false },
                         { status: 503 });
  }
});
