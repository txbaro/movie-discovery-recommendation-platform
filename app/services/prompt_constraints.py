import re
import unicodedata
from dataclasses import dataclass


# Canonical labels are intentionally Vietnamese because they are returned to
# the UI. Aliases cover the common Vietnamese and English values used by TMDB
# and the three cinema providers.
GENRE_ALIASES: dict[str, tuple[str, ...]] = {
    "Kinh dị": ("kinh di", "horror", "scary", "spooky"),
    "Hài": (
        "hai",
        "comedy",
        "funny",
        "humorous",
        "hilarious",
        "lighthearted",
    ),
    "Hành động": ("hanh dong", "action"),
    "Hoạt hình": ("hoat hinh", "animation", "anime"),
    "Tình cảm": ("tinh cam", "lang man", "romance", "romantic"),
    "Gia đình": ("gia dinh", "family"),
    "Giật gân": ("giat gan", "thriller"),
    "Khoa học viễn tưởng": (
        "khoa hoc vien tuong",
        "science fiction",
        "sci fi",
        "sci-fi",
    ),
    "Chính kịch": ("chinh kich", "drama"),
    "Phiêu lưu": ("phieu luu", "adventure"),
    "Kỳ ảo": ("ky ao", "gia tuong", "fantasy"),
    "Tội phạm": ("toi pham", "crime"),
    "Tài liệu": ("tai lieu", "documentary"),
}

_SOFT_NEGATION = re.compile(
    r"(?:khong\s+qua|not\s+too)(?:\s+[a-z0-9]+){0,3}\s*$"
)
_HARD_NEGATION = re.compile(
    r"(?:"
    r"khong(?:\s+(?:muon|thich|phai|co))?"
    r"|dung(?:\s+goi\s+y)?"
    r"|ne|tranh|loai\s+bo|tru|ngoai\s+tru"
    r"|no|not|without|exclude|avoid"
    r")(?:\s+(?:"
    r"xem|chon|goi|y|phim|the|loai|yeu|to|bat|ky|nao|la|mot|bo|mang|"
    r"movie|movies|genre"
    r")){0,6}\s*$"
)


@dataclass(frozen=True)
class PromptConstraints:
    included_genres: tuple[str, ...] = ()
    excluded_genres: tuple[str, ...] = ()
    soft_avoid_genres: tuple[str, ...] = ()


def normalize_constraint_text(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(
        re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).split()
    )


def _alias_occurrences(text: str, alias: str):
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])")
    return pattern.finditer(text)


def parse_prompt_constraints(prompt: str) -> PromptConstraints:
    normalized = normalize_constraint_text(prompt)
    included: set[str] = set()
    excluded: set[str] = set()
    soft_avoid: set[str] = set()

    for genre, aliases in GENRE_ALIASES.items():
        for alias in aliases:
            normalized_alias = normalize_constraint_text(alias)
            for match in _alias_occurrences(normalized, normalized_alias):
                prefix = normalized[: match.start()]
                if _SOFT_NEGATION.search(prefix):
                    soft_avoid.add(genre)
                elif _HARD_NEGATION.search(prefix):
                    excluded.add(genre)
                else:
                    included.add(genre)

    included -= excluded | soft_avoid
    soft_avoid -= excluded
    return PromptConstraints(
        included_genres=tuple(sorted(included)),
        excluded_genres=tuple(sorted(excluded)),
        soft_avoid_genres=tuple(sorted(soft_avoid)),
    )


def movie_has_genre(movie_genres: str | None, canonical_genre: str) -> bool:
    normalized = normalize_constraint_text(movie_genres or "")
    return any(
        next(_alias_occurrences(normalized, normalize_constraint_text(alias)), None)
        is not None
        for alias in GENRE_ALIASES.get(canonical_genre, ())
    )


def movie_matches_any_genre(
    movie_genres: str | None,
    canonical_genres: tuple[str, ...],
) -> bool:
    return any(movie_has_genre(movie_genres, genre) for genre in canonical_genres)


def movie_genre_match_count(
    movie_genres: str | None,
    canonical_genres: tuple[str, ...],
) -> int:
    """Count explicit requested genres present in a movie's provider metadata."""
    return sum(
        movie_has_genre(movie_genres, genre) for genre in canonical_genres
    )
