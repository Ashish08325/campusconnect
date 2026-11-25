-- CampusConnect v2 migration: directory fields, constraints, indexes, triggers, and RLS.
-- Apply in the Supabase SQL Editor after 20261125_event_directory.sql.

-- 1. Optional event directory columns used by the CampusConnect UI
ALTER TABLE public.events
  ADD COLUMN IF NOT EXISTS description text,
  ADD COLUMN IF NOT EXISTS location text,
  ADD COLUMN IF NOT EXISTS campus text,
  ADD COLUMN IF NOT EXISTS club_name text,
  ADD COLUMN IF NOT EXISTS is_featured boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS registered_count integer NOT NULL DEFAULT 0;

-- 2. Data integrity constraints (idempotent)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'events_capacity_positive'
      AND conrelid = 'public.events'::regclass
  ) THEN
    ALTER TABLE public.events
      ADD CONSTRAINT events_capacity_positive CHECK (capacity IS NULL OR capacity > 0);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'events_dates_valid'
      AND conrelid = 'public.events'::regclass
  ) THEN
    ALTER TABLE public.events
      ADD CONSTRAINT events_dates_valid CHECK (
        ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'events_status_valid'
      AND conrelid = 'public.events'::regclass
  ) THEN
    ALTER TABLE public.events
      ADD CONSTRAINT events_status_valid CHECK (
        status IS NULL OR lower(status) IN ('upcoming', 'open', 'ongoing', 'full', 'closed', 'cancelled')
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'registrations_user_event_unique'
      AND conrelid = 'public.registrations'::regclass
  ) THEN
    ALTER TABLE public.registrations
      ADD CONSTRAINT registrations_user_event_unique UNIQUE (user_id, event_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM public.registrations WHERE user_id IS NULL OR event_id IS NULL
  ) THEN
    ALTER TABLE public.registrations
      ALTER COLUMN user_id SET NOT NULL,
      ALTER COLUMN event_id SET NOT NULL;
  END IF;
END $$;

-- 3. Backfill registered_count from existing registrations
UPDATE public.events e
SET registered_count = COALESCE(counts.cnt, 0)
FROM (
  SELECT event_id, count(*)::integer AS cnt
  FROM public.registrations
  GROUP BY event_id
) counts
WHERE e.id = counts.event_id;

-- 4. Foreign key and filter indexes
CREATE INDEX IF NOT EXISTS events_organizer_id_idx ON public.events (organizer_id);
CREATE INDEX IF NOT EXISTS events_starts_at_idx ON public.events (starts_at);
CREATE INDEX IF NOT EXISTS events_category_idx ON public.events (category);
CREATE INDEX IF NOT EXISTS events_status_idx ON public.events (status);
CREATE INDEX IF NOT EXISTS registrations_user_id_idx ON public.registrations (user_id);
CREATE INDEX IF NOT EXISTS registrations_event_id_idx ON public.registrations (event_id);

-- 5. Private schema for trigger functions (not exposed via Data API)
CREATE SCHEMA IF NOT EXISTS private;
REVOKE ALL ON SCHEMA private FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION private.sync_event_registered_count()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  target_capacity integer;
  current_count integer;
BEGIN
  IF TG_OP = 'INSERT' THEN
    SELECT capacity, registered_count
      INTO target_capacity, current_count
    FROM public.events
    WHERE id = NEW.event_id
    FOR UPDATE;

    IF NOT FOUND THEN
      RAISE EXCEPTION 'Event not found' USING ERRCODE = '23503';
    END IF;

    IF target_capacity IS NOT NULL AND current_count >= target_capacity THEN
      RAISE EXCEPTION 'Event has reached maximum capacity' USING ERRCODE = '23514';
    END IF;

    UPDATE public.events
    SET registered_count = registered_count + 1
    WHERE id = NEW.event_id;
    RETURN NEW;
  ELSIF TG_OP = 'DELETE' THEN
    UPDATE public.events
    SET registered_count = GREATEST(0, registered_count - 1)
    WHERE id = OLD.event_id;
    RETURN OLD;
  END IF;
  RETURN NULL;
END;
$$;

REVOKE EXECUTE ON FUNCTION private.sync_event_registered_count() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS trg_registrations_sync_count ON public.registrations;
CREATE TRIGGER trg_registrations_sync_count
  AFTER INSERT OR DELETE ON public.registrations
  FOR EACH ROW
  EXECUTE FUNCTION private.sync_event_registered_count();

CREATE OR REPLACE FUNCTION private.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  user_role text;
BEGIN
  user_role := COALESCE(NULLIF(NEW.raw_user_meta_data ->> 'role', ''), 'student');
  IF user_role NOT IN ('student', 'organizer') THEN
    user_role := 'student';
  END IF;

  INSERT INTO public.users (id, email, role)
  VALUES (NEW.id, NEW.email, user_role)
  ON CONFLICT (id) DO NOTHING;

  RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION private.handle_new_auth_user() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION private.handle_new_auth_user();

-- 6. Row Level Security policies
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.registrations ENABLE ROW LEVEL SECURITY;

-- public.users: users can only read/insert/update their own profile
DROP POLICY IF EXISTS users_select_own ON public.users;
CREATE POLICY users_select_own ON public.users
  FOR SELECT TO authenticated
  USING ((select auth.uid()) = id);

DROP POLICY IF EXISTS users_insert_own ON public.users;
CREATE POLICY users_insert_own ON public.users
  FOR INSERT TO authenticated
  WITH CHECK ((select auth.uid()) = id);

DROP POLICY IF EXISTS users_update_own ON public.users;
CREATE POLICY users_update_own ON public.users
  FOR UPDATE TO authenticated
  USING ((select auth.uid()) = id)
  WITH CHECK ((select auth.uid()) = id);

-- public.events: public directory read; authenticated organizer write
DROP POLICY IF EXISTS events_select_public ON public.events;
CREATE POLICY events_select_public ON public.events
  FOR SELECT TO anon, authenticated
  USING (true);

DROP POLICY IF EXISTS events_insert_organizer ON public.events;
CREATE POLICY events_insert_organizer ON public.events
  FOR INSERT TO authenticated
  WITH CHECK ((select auth.uid()) = organizer_id);

DROP POLICY IF EXISTS events_update_organizer ON public.events;
CREATE POLICY events_update_organizer ON public.events
  FOR UPDATE TO authenticated
  USING ((select auth.uid()) = organizer_id)
  WITH CHECK ((select auth.uid()) = organizer_id);

DROP POLICY IF EXISTS events_delete_organizer ON public.events;
CREATE POLICY events_delete_organizer ON public.events
  FOR DELETE TO authenticated
  USING ((select auth.uid()) = organizer_id);

-- public.registrations: user sees own registrations, organizer sees registrations for their events
DROP POLICY IF EXISTS registrations_select_owner_or_organizer ON public.registrations;
CREATE POLICY registrations_select_owner_or_organizer ON public.registrations
  FOR SELECT TO authenticated
  USING (
    (select auth.uid()) = user_id
    OR EXISTS (
      SELECT 1 FROM public.events e
      WHERE e.id = event_id AND e.organizer_id = (select auth.uid())
    )
  );

DROP POLICY IF EXISTS registrations_insert_own ON public.registrations;
CREATE POLICY registrations_insert_own ON public.registrations
  FOR INSERT TO authenticated
  WITH CHECK ((select auth.uid()) = user_id);

DROP POLICY IF EXISTS registrations_delete_own ON public.registrations;
CREATE POLICY registrations_delete_own ON public.registrations
  FOR DELETE TO authenticated
  USING ((select auth.uid()) = user_id);
