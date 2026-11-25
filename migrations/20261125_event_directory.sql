-- Backward-compatible fields used by the CampusConnect event directory.
-- Existing rows receive the category default; event dates remain unknown until set.
ALTER TABLE public.events
  ADD COLUMN IF NOT EXISTS category text NOT NULL DEFAULT 'Other',
  ADD COLUMN IF NOT EXISTS starts_at timestamptz,
  ADD COLUMN IF NOT EXISTS ends_at timestamptz;
