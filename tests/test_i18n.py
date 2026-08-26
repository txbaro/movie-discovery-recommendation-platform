import pytest

from app.core.i18n import TRANSLATIONS, translate


def test_translation_catalogues_have_matching_keys():
    assert set(TRANSLATIONS["vi"]) == set(TRANSLATIONS["en"])
    assert translate("en", "nav.login") == "Sign in"
    assert translate("vi", "nav.login") == "Đăng nhập"


@pytest.mark.asyncio
async def test_language_switch_persists_cookie_and_safe_redirect(client):
    response = await client.get(
        "/language/en",
        params={"next": "/login?from=home"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login?from=home"
    assert "locale=en" in response.headers["set-cookie"]

    client.cookies.set("locale", "en")
    english = await client.get("/login")
    assert english.status_code == 200
    assert '<html lang="en">' in english.text
    assert "Sign in" in english.text
    assert "Forgot password?" in english.text

    unsafe = await client.get(
        "/language/en",
        params={"next": "//evil.example/phishing"},
        follow_redirects=False,
    )
    assert unsafe.headers["location"] == "/"


@pytest.mark.asyncio
async def test_vietnamese_remains_default_language(client):
    response = await client.get("/login")

    assert response.status_code == 200
    assert '<html lang="vi">' in response.text
    assert "Đăng nhập" in response.text


@pytest.mark.asyncio
async def test_english_localizes_primary_discovery_pages(client, catalogue):
    client.cookies.set("locale", "en")

    home = await client.get("/")
    nearby = await client.get("/nearby-cinemas")
    movie = await client.get(f"/movie/{catalogue['movie_id']}")
    seats = await client.get(f"/showtime/{catalogue['showtime_id']}/seats")
    bookings = await client.get("/my-bookings")

    assert "Find a movie to watch" in home.text
    assert "NATURAL-LANGUAGE RECOMMENDATIONS" in home.text
    assert "Find the nearest cinema" in nearby.text
    assert "Back to movies" in movie.text
    assert "Showtimes" in movie.text
    assert "CHOOSE SEATS" in seats.text
    assert "Realtime updates were interrupted" in seats.text
    assert "My tickets" in bookings.text
