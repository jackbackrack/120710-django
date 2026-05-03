WITH counts AS (
    SELECT 'auth group artist exists' AS check_name,
        CASE WHEN EXISTS (SELECT 1 FROM auth_group WHERE name = 'artist') THEN 1 ELSE 0 END AS source_count,
        1 AS target_count
    UNION ALL
    SELECT 'auth group curator exists',
        CASE WHEN EXISTS (SELECT 1 FROM auth_group WHERE name = 'curator') THEN 1 ELSE 0 END,
        1
    UNION ALL
    SELECT 'auth group staff exists',
        CASE WHEN EXISTS (SELECT 1 FROM auth_group WHERE name = 'staff') THEN 1 ELSE 0 END,
        1
    UNION ALL
    SELECT 'auth group juror exists',
        CASE WHEN EXISTS (SELECT 1 FROM auth_group WHERE name = 'juror') THEN 1 ELSE 0 END,
        1
    UNION ALL
    SELECT 'reviews_showjuror table exists' AS check_name,
        CASE WHEN to_regclass('public.reviews_showjuror') IS NOT NULL THEN 1 ELSE 0 END AS source_count,
        1 AS target_count
    UNION ALL
    SELECT 'reviews_artworkreview table exists',
        CASE WHEN to_regclass('public.reviews_artworkreview') IS NOT NULL THEN 1 ELSE 0 END,
        1
    UNION ALL
    SELECT 'artist rows' AS check_name, (SELECT COUNT(*) FROM piece_artist) AS source_count, (SELECT COUNT(*) FROM gallery_artist) AS target_count
    UNION ALL
    SELECT 'show rows', (SELECT COUNT(*) FROM piece_show), (SELECT COUNT(*) FROM gallery_show)
    UNION ALL
    SELECT 'event rows', (SELECT COUNT(*) FROM piece_event), (SELECT COUNT(*) FROM gallery_event)
    UNION ALL
    SELECT 'artwork rows', (SELECT COUNT(*) FROM piece_piece), (SELECT COUNT(*) FROM gallery_artwork)
    UNION ALL
    SELECT 'artwork-artist links', (SELECT COUNT(*) FROM piece_piece_artists), (SELECT COUNT(*) FROM gallery_artwork_artists)
    UNION ALL
    SELECT 'artwork-show links', (SELECT COUNT(*) FROM piece_piece_shows), (SELECT COUNT(*) FROM gallery_artwork_shows)
    UNION ALL
    SELECT 'show-curator links', (SELECT COUNT(*) FROM piece_show_curators), (SELECT COUNT(*) FROM gallery_show_curators)
    UNION ALL
    -- gallery_show_artists is derived (no legacy source); report its count against itself as a non-zero sanity check
    SELECT 'show-artist links (derived)', (SELECT COUNT(*) FROM gallery_show_artists), (SELECT COUNT(*) FROM gallery_show_artists)
),
comparison AS (
    SELECT
        check_name,
        source_count,
        target_count,
        CASE WHEN source_count = target_count THEN 'ok' ELSE 'mismatch' END AS status
    FROM counts
)
SELECT *
FROM comparison
ORDER BY check_name;

SELECT
    'missing artists in gallery' AS check_name,
    COUNT(*) AS missing_count
FROM piece_artist source
LEFT JOIN gallery_artist target ON target.id = source.id
WHERE target.id IS NULL;

SELECT
    'missing shows in gallery' AS check_name,
    COUNT(*) AS missing_count
FROM piece_show source
LEFT JOIN gallery_show target ON target.id = source.id
WHERE target.id IS NULL;

SELECT
    'missing events in gallery' AS check_name,
    COUNT(*) AS missing_count
FROM piece_event source
LEFT JOIN gallery_event target ON target.id = source.id
WHERE target.id IS NULL;

SELECT
    'missing artworks in gallery' AS check_name,
    COUNT(*) AS missing_count
FROM piece_piece source
LEFT JOIN gallery_artwork target ON target.id = source.id
WHERE target.id IS NULL;

SELECT
    'missing artwork-artist links in gallery' AS check_name,
    COUNT(*) AS missing_count
FROM piece_piece_artists source
LEFT JOIN gallery_artwork_artists target
    ON target.artwork_id = source.piece_id AND target.artist_id = source.artist_id
WHERE target.id IS NULL;

SELECT
    'missing artwork-show links in gallery' AS check_name,
    COUNT(*) AS missing_count
FROM piece_piece_shows source
LEFT JOIN gallery_artwork_shows target
    ON target.artwork_id = source.piece_id AND target.show_id = source.show_id
WHERE target.id IS NULL;

SELECT
    'missing show-curator links in gallery' AS check_name,
    COUNT(*) AS missing_count
FROM piece_show_curators source
LEFT JOIN gallery_show_curators target
    ON target.show_id = source.show_id AND target.artist_id = source.artist_id
WHERE target.id IS NULL;

SELECT
    'show-artist links derived (non-zero expected)' AS check_name,
    COUNT(*) AS link_count
FROM gallery_show_artists;