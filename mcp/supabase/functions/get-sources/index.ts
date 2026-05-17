import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS });
  }

  try {
    const url = Deno.env.get("SUPABASE_URL")!;
    const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const supabase = createClient(url, key);

    const reqUrl = new URL(req.url);
    const sourceId = reqUrl.searchParams.get("source_id");

    if (sourceId) {
      const { data, error } = await supabase
        .from("sources")
        .select("source_id, title, source_url, source_type, fetched_at, sha256, license, word_count")
        .eq("source_id", sourceId)
        .maybeSingle();
      if (error) throw error;
      return new Response(JSON.stringify(data ?? null), {
        headers: { ...CORS, "Content-Type": "application/json" },
      });
    }

    const { data, error } = await supabase
      .from("sources")
      .select("source_id, title, source_url, source_type, fetched_at, word_count")
      .eq("source_type", "url_ingest")
      .order("source_id");
    if (error) throw error;

    return new Response(JSON.stringify({ sources: data ?? [], count: (data ?? []).length }), {
      headers: { ...CORS, "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e?.message ?? e) }), {
      status: 500,
      headers: { ...CORS, "Content-Type": "application/json" },
    });
  }
});
