BEGIN;

DO $$
BEGIN
    IF to_regclass('public.piece_artist') IS NULL THEN
        RAISE EXCEPTION 'Missing source table: piece_artist';
    END IF;
    IF to_regclass('public.piece_show') IS NULL THEN
        RAISE EXCEPTION 'Missing source table: piece_show';
    END IF;
    IF to_regclass('public.piece_event') IS NULL THEN
        RAISE EXCEPTION 'Missing source table: piece_event';
    END IF;
    IF to_regclass('public.piece_piece') IS NULL THEN
        RAISE EXCEPTION 'Missing source table: piece_piece';
    END IF;

    IF to_regclass('public.gallery_artist') IS NULL THEN
        RAISE EXCEPTION 'Missing target table: gallery_artist';
    END IF;
    IF to_regclass('public.gallery_show') IS NULL THEN
        RAISE EXCEPTION 'Missing target table: gallery_show';
    END IF;
    IF to_regclass('public.gallery_event') IS NULL THEN
        RAISE EXCEPTION 'Missing target table: gallery_event';
    END IF;
    IF to_regclass('public.gallery_artwork') IS NULL THEN
        RAISE EXCEPTION 'Missing target table: gallery_artwork';
    END IF;
END $$;

DO $$
DECLARE
    has_first_name boolean;
    has_last_name boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'piece_artist' AND column_name = 'first_name'
    ) INTO has_first_name;

    SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'piece_artist' AND column_name = 'last_name'
    ) INTO has_last_name;

    IF has_first_name AND has_last_name THEN
        EXECUTE $sql$
            CREATE TEMP TABLE migration_piece_artist_source AS
            SELECT
                id,
                name,
                COALESCE(first_name, '') AS first_name,
                COALESCE(last_name, '') AS last_name,
                email,
                phone,
                website,
                instagram,
                bio,
                statement,
                image,
                created_at,
                user_id
            FROM piece_artist
        $sql$;
    ELSE
        EXECUTE $sql$
            CREATE TEMP TABLE migration_piece_artist_source AS
            SELECT
                id,
                name,
                CASE
                    WHEN btrim(COALESCE(name, '')) = '' THEN ''
                    ELSE split_part(btrim(name), ' ', 1)
                END AS first_name,
                CASE
                    WHEN strpos(btrim(COALESCE(name, '')), ' ') > 0 THEN regexp_replace(btrim(name), '^\S+\s*', '')
                    ELSE ''
                END AS last_name,
                email,
                phone,
                website,
                instagram,
                bio,
                statement,
                image,
                created_at,
                user_id
            FROM piece_artist
        $sql$;
    END IF;
END $$;

TRUNCATE TABLE
    gallery_artwork_artists,
    gallery_artwork_shows,
    gallery_show_curators,
    gallery_event,
    gallery_artwork,
    gallery_show,
    gallery_artist
RESTART IDENTITY CASCADE;

INSERT INTO gallery_artist (
    id,
    name,
    first_name,
    last_name,
    email,
    phone,
    website,
    instagram,
    bio,
    statement,
    image,
    created_at,
    user_id
)
SELECT
    id,
    name,
    first_name,
    last_name,
    email,
    phone,
    website,
    instagram,
    bio,
    statement,
    image,
    created_at,
    user_id
FROM migration_piece_artist_source
ORDER BY id;

INSERT INTO gallery_show (
    id,
    name,
    description,
    image,
    start,
    "end",
    created_at
)
SELECT
    id,
    name,
    description,
    image,
    start,
    "end",
    created_at
FROM piece_show
ORDER BY id;

INSERT INTO gallery_event (
    id,
    name,
    description,
    show_id,
    image,
    date,
    start,
    "end",
    created_at
)
SELECT
    id,
    name,
    description,
    show_id,
    image,
    date,
    start,
    "end",
    created_at
FROM piece_event
ORDER BY id;

INSERT INTO gallery_artwork (
    id,
    name,
    end_year,
    start_year,
    medium,
    dimensions,
    image,
    price,
    pricing,
    replacement_cost,
    is_sold,
    description,
    installation,
    created_at
)
SELECT
    id,
    name,
    end_year,
    start_year,
    medium,
    dimensions,
    image,
    price,
    pricing,
    replacement_cost,
    is_sold,
    description,
    installation,
    created_at
FROM piece_piece
ORDER BY id;

INSERT INTO gallery_artwork_artists (
    id,
    artwork_id,
    artist_id
)
SELECT
    id,
    piece_id,
    artist_id
FROM piece_piece_artists
ORDER BY id;

INSERT INTO gallery_artwork_shows (
    id,
    artwork_id,
    show_id
)
SELECT
    id,
    piece_id,
    show_id
FROM piece_piece_shows
ORDER BY id;

INSERT INTO gallery_show_curators (
    id,
    show_id,
    artist_id
)
SELECT
    id,
    show_id,
    artist_id
FROM piece_show_curators
ORDER BY id;

SELECT setval(pg_get_serial_sequence('gallery_artist', 'id'), COALESCE((SELECT MAX(id) FROM gallery_artist), 1), (SELECT COUNT(*) > 0 FROM gallery_artist));
SELECT setval(pg_get_serial_sequence('gallery_show', 'id'), COALESCE((SELECT MAX(id) FROM gallery_show), 1), (SELECT COUNT(*) > 0 FROM gallery_show));
SELECT setval(pg_get_serial_sequence('gallery_event', 'id'), COALESCE((SELECT MAX(id) FROM gallery_event), 1), (SELECT COUNT(*) > 0 FROM gallery_event));
SELECT setval(pg_get_serial_sequence('gallery_artwork', 'id'), COALESCE((SELECT MAX(id) FROM gallery_artwork), 1), (SELECT COUNT(*) > 0 FROM gallery_artwork));
SELECT setval(pg_get_serial_sequence('gallery_artwork_artists', 'id'), COALESCE((SELECT MAX(id) FROM gallery_artwork_artists), 1), (SELECT COUNT(*) > 0 FROM gallery_artwork_artists));
SELECT setval(pg_get_serial_sequence('gallery_artwork_shows', 'id'), COALESCE((SELECT MAX(id) FROM gallery_artwork_shows), 1), (SELECT COUNT(*) > 0 FROM gallery_artwork_shows));
SELECT setval(pg_get_serial_sequence('gallery_show_curators', 'id'), COALESCE((SELECT MAX(id) FROM gallery_show_curators), 1), (SELECT COUNT(*) > 0 FROM gallery_show_curators));

COMMIT;