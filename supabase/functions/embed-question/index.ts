// embed-question: turns a user's question into an OpenAI embedding vector.
// The browser can't call OpenAI directly (that would expose our API key),
// so it calls this function, which holds the key as a server-side secret.
//
// Request:  POST { "question": "..." }  with the user's Authorization: Bearer <jwt>
// Response: { "embedding": [ ...1536 numbers ] }

import { requireUser } from '../_shared/auth.ts';
import { corsHeaders, jsonResponse } from '../_shared/cors.ts';

const EMBEDDING_MODEL = 'text-embedding-3-small';
const MAX_QUESTION_LENGTH = 2000;

Deno.serve(async (req) => {
  // Browsers send an OPTIONS "preflight" request before the real cross-origin POST
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders });
  }

  if (req.method !== 'POST') {
    return jsonResponse({ error: 'Method not allowed' }, 405);
  }

  // --- Verify the user is logged in ---
  // We deploy with --no-verify-jwt, so we check the token ourselves.
  const user = await requireUser(req);
  if (user instanceof Response) return user;

  // --- Validate input ---
  let question: unknown;
  try {
    ({ question } = await req.json());
  } catch {
    return jsonResponse({ error: 'Request body must be JSON' }, 400);
  }

  if (typeof question !== 'string' || question.trim() === '') {
    return jsonResponse({ error: 'Body must include a non-empty "question" string' }, 400);
  }
  if (question.length > MAX_QUESTION_LENGTH) {
    return jsonResponse({ error: `Question must be under ${MAX_QUESTION_LENGTH} characters` }, 400);
  }

  // --- Call OpenAI ---
  const openaiKey = Deno.env.get('OPENAI_API_KEY');
  if (!openaiKey) {
    return jsonResponse({ error: 'OPENAI_API_KEY secret is not set' }, 500);
  }

  try {
    const response = await fetch('https://api.openai.com/v1/embeddings', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${openaiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ model: EMBEDDING_MODEL, input: question.trim() }),
    });

    if (!response.ok) {
      // Log details server-side, but don't leak OpenAI's raw error to the browser
      console.error('OpenAI error:', response.status, await response.text());
      return jsonResponse({ error: 'Failed to generate embedding' }, 502);
    }

    const result = await response.json();
    return jsonResponse({ embedding: result.data[0].embedding });
  } catch (err) {
    console.error('embed-question failed:', err);
    return jsonResponse({ error: 'Failed to generate embedding' }, 500);
  }
});
