-- Allow 'supersedes' as an issue_dependency type (evolution path edges for
-- the project map view). The live constraint already carried the internal
-- agent-written types (implements/tests/...), so preserve them all here.
ALTER TABLE issue_dependency DROP CONSTRAINT issue_dependency_type_check;
ALTER TABLE issue_dependency ADD CONSTRAINT issue_dependency_type_check
    CHECK (type IN ('blocks', 'blocked_by', 'related', 'implements', 'tests',
                    'references', 'reviews', 'spawns', 'handoff_to',
                    'duplicates', 'part_of', 'decomposes', 'supersedes'));
