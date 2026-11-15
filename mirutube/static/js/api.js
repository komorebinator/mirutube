// Requests to the server. Videos and playlists are named by their short ids
// from the catalogue (decisions#short-ids).

export function idUrl(prefix, id) {
  return prefix + encodeURIComponent(id);
}

export async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(url + ": " + res.status);
  return res.json();
}

// A POST whose answer the caller needs, failure included:
// {ok, status, data} with the parsed JSON, or with the server's message as
// `error` when it refused.
export async function request(url, data) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}),
    });
    if (res.ok) return { ok: true, status: res.status, data: await res.json() };
    const text = await res.text().catch(() => "");
    return { ok: false, status: res.status, error: text || String(res.status) };
  } catch (e) {
    return { ok: false, status: 0, error: "the server could not be reached" };
  }
}

// A POST sent and not waited on — a report, a choice, a command: the JSON
// answered, or null. `keepalive` lets one sent as the page goes still arrive.
export function postJSON(url, data) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    keepalive: true,
  }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
}
