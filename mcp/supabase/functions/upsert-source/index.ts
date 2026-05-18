// upsert-source: simple endpoint to insert/update a source row.
// Gated by x-ingest-secret. Used by the voynich.ninja crawler before passages.

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

const INGEST_SECRET = Deno.env.get("INGEST_SECRET") ?? "research-agent-prototype-2026";

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "POST only" }), { status: 405 });
  }
  if (req.headers.get("x-ingest-secret") !== INGEST_SECRET) {
    return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  }

  let body: {
    source_id: string;
    title: string;
    source_url?: string;
    source_citation?: string;
    source_type?: string;
    license?: string;
    word_count?: number;
  };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid json" }), { status: 400 });
  }

  if (!body.source_id || !body.title) {
    return new Response(JSON.stringify({ error: "source_id and title required" }), { status: 400 });
  }

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
  );

  const { error } = await supabase.from("sources").upsert({
    source_id: body.source_id,
    title: body.title.slice(0, 500),
    source_url: body.source_url ?? null,
    source_citation: body.source_citation ?? null,
    source_type: body.source_type ?? "curated",
    fetched_at: new Date().toISOString(),
    license: body.license ?? null,
    word_count: body.word_count ?? null,
  });

  if (error) {
    return new Response(
      JSON.stringify({ error: "upsert failed", detail: error.message }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }

  return new Response(
    JSON.stringify({ source_id: body.source_id, status: "ok" }),
    { status: 200, headers: { "Content-Type": "application/json" } }
  );
});
