// generate-answer: the "G" in RAG. Takes the user's question plus the conference
// talks retrieved by vector search, and asks GPT-4o to answer using only those talks.
//
// Request:  POST { "question": "...", "context_talks": [{ title, speaker, text }, ...] }
//           with the user's Authorization: Bearer <jwt>
// Response: { "answer": "..." }

import { requireUser } from '../_shared/auth.ts';
import { corsHeaders, jsonResponse } from '../_shared/cors.ts';

const CHAT_MODEL = 'gpt-4o';
const MAX_QUESTION_LENGTH = 2000;
const MAX_TALKS = 5;
// Cap each talk so a few long talks can't blow up the prompt size (and cost)
const MAX_TALK_CHARS = 30000;

const SYSTEM_PROMPT = `You answer questions using excerpts from General Conference talks of The Church of Jesus Christ of Latter-day Saints.

Rules:
- Base your answer ONLY on the provided talks. Do not add outside facts or quotes.
- Cite the talks you draw from by title and speaker, e.g. (“Title” — Speaker).
- Quote short phrases from the talks where they help, and attribute them.
- If the talks don't address the question, say so plainly instead of guessing.
- Write in plain text paragraphs (no Markdown headings, bullets, or bold), about 150–300 words.`;

interface ContextTalk {
  title: string;
  speaker: string;
  text: string;
}

function isContextTalk(t: unknown): t is ContextTalk {
  const talk = t as ContextTalk;
  return typeof talk?.title === 'string' &&
    typeof talk?.speaker === 'string' &&
    typeof talk?.text === 'string';
}

// Number each talk so GPT can tell them apart and cite them
function buildContext(talks: ContextTalk[]): string {
  return talks.map((talk, i) =>
    `[Talk ${i + 1}] "${talk.title}" — ${talk.speaker}\n${talk.text.slice(0, MAX_TALK_CHARS)}`
  ).join('\n\n---\n\n');
}

Deno.serve(async (req) => {
  // Browsers send an OPTIONS "preflight" request before the real cross-origin POST
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders });
  }

  if (req.method !== 'POST') {
    return jsonResponse({ error: 'Method not allowed' }, 405);
  }

  // --- Verify the user is logged in ---
  const user = await requireUser(req);
  if (user instanceof Response) return user;

  // --- Validate input ---
  let question: unknown;
  let contextTalks: unknown;
  try {
    ({ question, context_talks: contextTalks } = await req.json());
  } catch {
    return jsonResponse({ error: 'Request body must be JSON' }, 400);
  }

  if (typeof question !== 'string' || question.trim() === '') {
    return jsonResponse({ error: 'Body must include a non-empty "question" string' }, 400);
  }
  if (question.length > MAX_QUESTION_LENGTH) {
    return jsonResponse({ error: `Question must be under ${MAX_QUESTION_LENGTH} characters` }, 400);
  }
  // The site's readiness check sends an empty list; rejecting it here avoids paying for a GPT call
  if (!Array.isArray(contextTalks) || contextTalks.length === 0) {
    return jsonResponse({ error: '"context_talks" must be a non-empty array' }, 400);
  }
  if (!contextTalks.every(isContextTalk)) {
    return jsonResponse({ error: 'Each context talk needs string "title", "speaker", and "text" fields' }, 400);
  }

  // --- Call OpenAI ---
  const openaiKey = Deno.env.get('OPENAI_API_KEY');
  if (!openaiKey) {
    return jsonResponse({ error: 'OPENAI_API_KEY secret is not set' }, 500);
  }

  const talks = contextTalks.slice(0, MAX_TALKS);

  try {
    const response = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${openaiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: CHAT_MODEL,
        temperature: 0.3,
        max_tokens: 800,
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          {
            role: 'user',
            content: `Conference talks:\n\n${buildContext(talks)}\n\n---\n\nQuestion: ${question.trim()}`,
          },
        ],
      }),
    });

    if (!response.ok) {
      // Log details server-side, but don't leak OpenAI's raw error to the browser
      console.error('OpenAI error:', response.status, await response.text());
      return jsonResponse({ error: 'Failed to generate answer' }, 502);
    }

    const result = await response.json();
    const answer = result.choices?.[0]?.message?.content?.trim();
    if (!answer) {
      return jsonResponse({ error: 'Failed to generate answer' }, 502);
    }

    return jsonResponse({ answer });
  } catch (err) {
    console.error('generate-answer failed:', err);
    return jsonResponse({ error: 'Failed to generate answer' }, 500);
  }
});
