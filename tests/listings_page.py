"""The Listings page as the browser gets it: the rendered template plus its
own scripts (static/listings-*.js and static/listings.js, Batch 3.1), as one
text for structural checks. Behaviour is checked in tests/test_browser_listings.py."""

LISTINGS_SCRIPTS = ("listing-notes.js", "listing-details-cache.js", "listings-saved-searches.js", "listings-view.js",
                    "listings-summary.js", "listings-details.js", "listings-cards.js", "listings-notes.js",
                    "listings-drawer.js", "listings-comparison-store.js", "listings-comparison-render.js",
                    "listings-comparison.js", "listings-map-model.js", "listings-map.js", "listings.js")


def listings_source(client) -> str:
    parts = [client.get("/").get_data(as_text=True)]
    for name in LISTINGS_SCRIPTS:
        r = client.get(f"/static/{name}")
        try:
            parts.append(r.get_data(as_text=True))
        finally:
            r.close()
    # A Windows checkout has CRLF line endings; compare as LF.
    return "\n".join(parts).replace("\r\n", "\n")
