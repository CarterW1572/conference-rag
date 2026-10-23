"""
Step 7 Feature: Search Analytics Schema
=========================================
Creates the search_logs table, its Row Level Security policies, and the
get_search_analytics() function that powers the 📊 Analytics dashboard
(popular questions + frequently matched talks).

Security design:
    - Logged-in users can INSERT only rows tagged with their own user_id,
      and can SELECT only their own raw rows (RLS).
    - Cross-user statistics come from get_search_analytics(), a
      SECURITY DEFINER function that returns aggregates only — never
      user IDs or emails — and refuses to run for anonymous callers.
    - Talk titles/speakers/URLs are looked up from sentence_embeddings
      inside the function, so the dashboard never renders talk metadata
      supplied by a browser.

Usage:
    python scripts/06_create_analytics.py

Prerequisites:
    - config.public.json with Supabase URL and anon key
    - config.secret.json with Supabase service key, access token, and project ref
    - Database schema created (scripts/01_create_schema.py)
"""

import json
import sys
import time

# Load configuration from both config files
with open('config.public.json', 'r') as f:
    public_config = json.load(f)
with open('config.secret.json', 'r') as f:
    secrets = json.load(f)

SUPABASE_URL = public_config['SUPABASE_URL']
SUPABASE_SERVICE_KEY = secrets['SUPABASE_SERVICE_KEY']
SUPABASE_ACCESS_TOKEN = secrets['SUPABASE_ACCESS_TOKEN']
SUPABASE_PROJECT_REF = secrets['SUPABASE_PROJECT_REF']


def create_analytics_schema():
    import requests
    from supabase import create_client

    print("=" * 60)
    print("Creating Search Analytics Schema")
    print("=" * 60)

    schema_sql = r"""
-- One row per search a logged-in user runs from the site
CREATE TABLE IF NOT EXISTS search_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
    search_type TEXT NOT NULL CHECK (search_type IN ('keyword', 'semantic', 'rag')),
    query TEXT NOT NULL CHECK (char_length(query) BETWEEN 1 AND 500),
    -- Lowercased, whitespace-collapsed, trailing ?.! removed, so
    -- "What is faith?" and "what is faith" count as the same question
    normalized_query TEXT GENERATED ALWAYS AS (
        lower(regexp_replace(regexp_replace(btrim(query), '\s+', ' ', 'g'), '[?.!]+$', ''))
    ) STORED,
    -- Ranked list of matched talks: [{ "talk_id": "...", "similarity": 0.62 }, ...]
    matched_talks JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
        jsonb_typeof(matched_talks) = 'array' AND jsonb_array_length(matched_talks) <= 20
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS search_logs_created_at_idx ON search_logs(created_at);
CREATE INDEX IF NOT EXISTS search_logs_user_id_idx ON search_logs(user_id);

-- Enable Row Level Security
ALTER TABLE search_logs ENABLE ROW LEVEL SECURITY;

-- Users can only log searches as themselves
DROP POLICY IF EXISTS "Users can log their own searches" ON search_logs;
CREATE POLICY "Users can log their own searches"
ON search_logs FOR INSERT
TO authenticated
WITH CHECK (user_id = auth.uid());

-- Users can only read their own raw search history
DROP POLICY IF EXISTS "Users can read their own searches" ON search_logs;
CREATE POLICY "Users can read their own searches"
ON search_logs FOR SELECT
TO authenticated
USING (user_id = auth.uid());

-- Aggregated analytics for the dashboard.
-- SECURITY DEFINER lets it count everyone's searches (bypassing the per-user
-- SELECT policy), so it must only ever return aggregates.
CREATE OR REPLACE FUNCTION get_search_analytics(
    p_days INT DEFAULT 30,        -- 0 or NULL = all time
    p_only_mine BOOLEAN DEFAULT false,
    p_limit INT DEFAULT 10
)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_uid UUID := auth.uid();
    v_since TIMESTAMPTZ;
    v_chart_start DATE;
    v_result JSONB;
BEGIN
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'Not authenticated' USING ERRCODE = '42501';
    END IF;

    p_limit := LEAST(GREATEST(COALESCE(p_limit, 10), 1), 50);
    IF p_days IS NULL OR p_days <= 0 THEN
        v_since := '-infinity';
    ELSE
        v_since := NOW() - make_interval(days => LEAST(p_days, 3650));
    END IF;

    -- Daily chart: the selected range, or (for all time) since the first search; capped at 90 days
    IF p_days IS NULL OR p_days <= 0 THEN
        SELECT COALESCE(MIN(created_at)::date, CURRENT_DATE) INTO v_chart_start
        FROM search_logs WHERE (NOT p_only_mine OR user_id = v_uid);
    ELSE
        v_chart_start := CURRENT_DATE - (LEAST(p_days, 90) - 1);
    END IF;
    v_chart_start := GREATEST(v_chart_start, CURRENT_DATE - 89);

    WITH logs AS (
        SELECT * FROM search_logs
        WHERE created_at >= v_since
          AND (NOT p_only_mine OR user_id = v_uid)
    ),
    -- One row per (search, matched talk), keeping the talk's rank in that search
    log_talks AS (
        SELECT l.id AS log_id, t.ord, t.talk
        FROM logs l
        CROSS JOIN LATERAL jsonb_array_elements(l.matched_talks) WITH ORDINALITY AS t(talk, ord)
        WHERE jsonb_typeof(t.talk) = 'object'
          AND t.talk->>'talk_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),
    top_talks AS (
        SELECT
            (talk->>'talk_id')::uuid AS talk_id,
            COUNT(DISTINCT log_id) AS times_matched,
            COUNT(DISTINCT log_id) FILTER (WHERE ord = 1) AS times_top,
            AVG(CASE WHEN jsonb_typeof(talk->'similarity') = 'number'
                     THEN (talk->>'similarity')::float END) AS avg_similarity
        FROM log_talks
        GROUP BY 1
        ORDER BY 2 DESC, 3 DESC
        LIMIT p_limit
    ),
    popular AS (
        SELECT
            (ARRAY_AGG(query ORDER BY created_at DESC))[1] AS question,
            COUNT(*) AS times_asked,
            COUNT(DISTINCT user_id) AS users,
            ARRAY_AGG(DISTINCT search_type) AS search_types,
            MAX(created_at) AS last_asked
        FROM logs
        GROUP BY normalized_query
        ORDER BY 2 DESC, 5 DESC
        LIMIT p_limit
    ),
    daily AS (
        SELECT d.day::date AS day, COUNT(l.id) AS searches
        FROM generate_series(v_chart_start, CURRENT_DATE, INTERVAL '1 day') AS d(day)
        LEFT JOIN logs l ON l.created_at::date = d.day::date
        GROUP BY 1
    )
    SELECT jsonb_build_object(
        'summary', (
            SELECT jsonb_build_object(
                'total_searches', COUNT(*),
                'unique_questions', COUNT(DISTINCT normalized_query),
                'active_users', COUNT(DISTINCT user_id),
                'keyword', COUNT(*) FILTER (WHERE search_type = 'keyword'),
                'semantic', COUNT(*) FILTER (WHERE search_type = 'semantic'),
                'rag', COUNT(*) FILTER (WHERE search_type = 'rag')
            )
            FROM logs
        ),
        'daily', COALESCE((
            SELECT jsonb_agg(jsonb_build_object('day', day, 'searches', searches) ORDER BY day)
            FROM daily
        ), '[]'::jsonb),
        'popular_questions', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'question', question,
                'times_asked', times_asked,
                'users', users,
                'search_types', search_types,
                'last_asked', last_asked
            ) ORDER BY times_asked DESC, last_asked DESC)
            FROM popular
        ), '[]'::jsonb),
        -- Talk metadata comes from sentence_embeddings, not from the logged JSON
        'top_talks', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'title', s.title,
                'speaker', s.speaker,
                'url', s.url,
                'times_matched', t.times_matched,
                'times_top', t.times_top,
                'avg_similarity', t.avg_similarity
            ) ORDER BY t.times_matched DESC, t.times_top DESC)
            FROM top_talks t
            CROSS JOIN LATERAL (
                SELECT se.title, se.speaker, se.url
                FROM sentence_embeddings se
                WHERE se.talk_id = t.talk_id
                LIMIT 1
            ) s
        ), '[]'::jsonb)
    ) INTO v_result;

    RETURN v_result;
END;
$$;

-- Only logged-in users may call it (Supabase grants EXECUTE to anon by default)
REVOKE ALL ON FUNCTION get_search_analytics(INT, BOOLEAN, INT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION get_search_analytics(INT, BOOLEAN, INT) TO authenticated;
"""

    url = f"https://api.supabase.com/v1/projects/{SUPABASE_PROJECT_REF}/database/query"
    headers = {
        "Authorization": f"Bearer {SUPABASE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    resp = requests.post(url, headers=headers, json={"query": schema_sql})
    if resp.status_code in (200, 201):
        print("✅ Analytics schema created successfully!")
    else:
        print(f"❌ Analytics schema creation failed: {resp.status_code}")
        print(resp.text[:500])
        return False

    # Verify table exists via PostgREST
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    for attempt in range(5):
        try:
            result = client.table('search_logs').select('id', count='exact').limit(1).execute()
            print(f"✅ Table verified. Current rows: {result.count or 0}")
            return True
        except Exception:
            if attempt < 4:
                print(f"   Waiting for schema cache to refresh... ({attempt + 1}/5)")
                time.sleep(3)
            else:
                print("⚠️  Table created but PostgREST cache hasn't refreshed yet.")
                print("   Give it a minute before loading the dashboard.")
                return True


if __name__ == '__main__':
    if not create_analytics_schema():
        sys.exit(1)
    print("\n✅ Analytics ready! Refresh your site and open 📊 Analytics.")
