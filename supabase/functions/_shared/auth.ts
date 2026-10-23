// Shared auth check for Edge Functions deployed with --no-verify-jwt.
// getUser() asks Supabase Auth to validate the JWT, so a forged or expired token is rejected.

import { createClient, type User } from 'jsr:@supabase/supabase-js@2';
import { jsonResponse } from './cors.ts';

// Returns the logged-in user, or a 401 Response to send back to the browser
export async function requireUser(req: Request): Promise<User | Response> {
  const authHeader = req.headers.get('Authorization');
  if (!authHeader?.startsWith('Bearer ')) {
    return jsonResponse({ error: 'Missing Authorization header' }, 401);
  }

  const supabase = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_ANON_KEY')!,
  );
  const token = authHeader.replace('Bearer ', '');
  const { data: { user }, error } = await supabase.auth.getUser(token);
  if (error || !user) {
    return jsonResponse({ error: 'Not authenticated' }, 401);
  }

  return user;
}
