WITH counts AS (
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