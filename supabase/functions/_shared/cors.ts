// Shared CORS headers so the browser (GitHub Pages / localhost) can call our Edge Functions.
// supabase-js sends `authorization`, `apikey`, `content-type`, and `x-client-info` headers,
// so all of them must be allowed or the browser's preflight request will fail.
export const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};

// Helper for returning JSON with CORS headers attached
export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, 'Content-Type': 'application/json' },
  });
}
