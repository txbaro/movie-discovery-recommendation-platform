import pytest

from app.services.prompt_constraints import (
    movie_has_genre,
    parse_prompt_constraints,
)


@pytest.mark.parametrize(
    ("prompt", "included", "excluded", "soft_avoid"),
    [
        (
            "Tôi muốn phim hài nhưng không muốn xem phim kinh dị",
            ("Hài",),
            ("Kinh dị",),
            (),
        ),
        (
            "Phim hồi hộp nhưng không quá kinh dị",
            (),
            (),
            ("Kinh dị",),
        ),
        (
            "Comedy without horror",
            ("Hài",),
            ("Kinh dị",),
            (),
        ),
        (
            "Tôi không buồn và muốn xem phim kinh dị",
            ("Kinh dị",),
            (),
            (),
        ),
    ],
)
def test_parse_prompt_genre_constraints(prompt, included, excluded, soft_avoid):
    result = parse_prompt_constraints(prompt)

    assert result.included_genres == included
    assert result.excluded_genres == excluded
    assert result.soft_avoid_genres == soft_avoid


def test_movie_genre_matching_supports_provider_languages():
    assert movie_has_genre("Horror,Thriller", "Kinh dị") is True
    assert movie_has_genre("Kinh Dị, Tâm Lý", "Kinh dị") is True
    assert movie_has_genre("Comedy,Family", "Kinh dị") is False
