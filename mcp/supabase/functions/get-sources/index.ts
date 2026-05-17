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
    const paraIndex = reqUrl.searchParams.get("paragraph_index");

    if (!sourceId) {
      return new Response(JSON.stringify({ error: "source_id required" }), {
        status: 400,
        headers: { ...CORS, "Content-Type": "application/json" },
      });
    }

    const { data: source, error: sErr } = await supabase
      .from("sources")
      .select("source_id, title, source_url, source_type, fetched_at, sha256, license, word_count")
      .eq("source_id", sourceId)
      .maybeSingle();
    if (sErr) throw sErr;
    if (!source) {
      return new Response(JSON.stringify({ error: `unknown source: ${sourceId}` }), {
        status: 404,
        headers: { ...CORS, "Content-Type": "application/json" },
      });
    }

    if (paraIndex !== null) {
      const idx = parseInt(paraIndex, 10);
      if (Number.isNaN(idx) || idx < 0) {
        return new Response(JSON.stringify({ error: "paragraph_index must be a non-negative integer" }), {
          status: 400,
          headers: { ...CORS, "Content-Type": "application/json" },
        });
      }
      const { data: para, error: pErr } = await supabase
        .from("source_passages")
        .select("source_id, paragraph_index, text")
        .eq("source_id", sourceId)
        .eq("paragraph_index", idx)
        .maybeSingle();
      if (pErr) throw pErr;
      if (!para) {
        const { count } = await supabase
          .from("source_passages")
          .select("*", { count: "exact", head: true })
          .eq("source_id", sourceId);
        return new Response(
          JSON.stringify({ error: `paragraph_index out of range (have ${count ?? 0} paragraphs)` }),
          { status: 404, headers: { ...CORS, "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify({ source, passage: para }), {
        headers: { ...CORS, "Content-Type": "application/json" },
      });
    }

    const { data: paras, error: pErr } = await supabase
      .from("source_passages")
      .select("source_id, paragraph_index, text")
      .eq("source_id", sourceId)
      .order("paragraph_index");
    if (pErr) throw pErr;

    return new Response(JSON.stringify({ source, passages: paras ?? [], count: (paras ?? []).length }), {
      headers: { ...CORS, "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e?.message ?? e) }), {
      status: 500,
      headers: { ...CORS, "Content-Type": "application/json" },
    });
  }
});
