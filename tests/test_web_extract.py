"""`web_fetch` returns the page's MAIN content, and says when it left something out.

A fetched documentation page used to arrive as its menu, sidebar, cookie banner, article and footer
links in document order. What has to hold for the cut to be safe:

  - the article is kept and the boilerplate is gone, and the result says how much was left out;
  - a fallback to the wider view whenever the cut would leave a stub: a teaser `<article>`, a page
    whose content sits in an `<aside>`, a page too short to judge;
  - a layout wrapper that merely NAMES a sidebar (`with-sidebar`, `<body class="sidebar">`) is never
    removed: only exact boilerplate names are;
  - unclosed tags do not make a skipped subtree swallow the rest of the document;
  - a page that is a JavaScript shell is named as one.
"""

from __future__ import annotations

from harness.integrations.web.extract import _html_to_markdown, extract

BODY = "The retry policy waits twice as long after each failure and gives up after five attempts. " * 6

DOC = f"""<!doctype html><html><head><title>Docs</title><script>var x=1</script></head>
<body class="has-sidebar">
<header><a href="/">Acme</a></header>
<nav><ul><li><a href="/a">Home</a><li><a href="/b">Pricing</a><li><a href="/c">Blog</a></ul></nav>
<div class="cookie-banner">We use cookies. Accept all.</div>
<div class="layout with-sidebar">
<aside class="sidebar"><a href="/x">Getting started</a><a href="/y">API</a></aside>
<main><h1>Retries</h1><p>{BODY}</p><pre><code>retry(max=5)</code></pre></main>
</div>
<footer><a href="/t">Terms</a> <a href="/p">Privacy</a> © Acme</footer>
</body></html>"""


def test_the_article_is_kept_and_the_menu_the_banner_the_sidebar_and_the_footer_are_not():
    page = extract(DOC)
    assert page.scope == "main"
    assert "# Retries" in page.text and "retry(max=5)" in page.text and "gives up after five" in page.text
    for left_out in ("Pricing", "We use cookies", "Getting started", "Privacy"):
        assert left_out not in page.text
    assert page.omitted_chars > 0  # the result can say how much it left out


def test_the_whole_page_is_still_available():
    full = extract(DOC, scope="full")
    assert full.scope == "full" and full.omitted_chars == 0
    for kept in ("Pricing", "We use cookies", "Getting started", "Privacy", "# Retries"):
        assert kept in full.text


def test_without_a_main_region_the_boilerplate_goes_and_the_rest_stays():
    page = extract(
        f"<html><body><nav><a href='/'>Home</a></nav><div class='post'><h1>Post</h1><p>{BODY}</p></div>"
        "<footer>(c) Acme</footer></body></html>"
    )
    assert page.scope == "clean"
    assert "Post" in page.text and "(c) Acme" not in page.text and "Home" not in page.text


def test_a_teaser_article_does_not_stand_for_a_page_that_is_mostly_something_else():
    page = extract(
        f"<html><body><div><h1>Real page</h1><p>{BODY}{BODY}</p></div>"
        "<article>Related: a short teaser here.</article></body></html>"
    )
    assert page.scope != "main" and "Real page" in page.text


def test_content_kept_in_an_aside_is_not_lost_to_the_cut():
    page = extract(f"<html><body><nav>menu</nav><aside><h1>All of the content</h1><p>{BODY}{BODY}</p></aside></body></html>")
    assert page.scope == "full" and "All of the content" in page.text


def test_a_short_page_is_returned_whole():
    page = extract("<html><body><nav>Menu</nav><main>Hi there.</main><footer>Foot</footer></body></html>")
    assert page.scope == "full" and "Menu" in page.text and "Foot" in page.text


def test_a_name_that_only_mentions_a_sidebar_does_not_remove_the_content():
    """`with-sidebar` wraps the content in a real layout; `<body class="sidebar">` is the page."""
    kept = extract(f"<html><body class='sidebar'><div class='with-sidebar'><main><h1>T</h1><p>{BODY}</p></main></div></body></html>")
    assert "retry policy" in kept.text


def test_a_hidden_subtree_with_unclosed_children_ends_at_its_own_end_tag():
    """A depth counter would stay "inside" the hidden list forever once its `<li>` were never closed,
    and everything after it would vanish."""
    page = extract(
        f"<html><body><ul hidden><li>alpha<li>beta</ul><p>{BODY}</p><p>After the list</p></body></html>",
        scope="full",
    )
    assert "After the list" in page.text and "gives up after five" in page.text
    assert "alpha" not in page.text and "beta" not in page.text


def test_a_javascript_shell_is_named_as_one():
    shell = extract(
        "<html><head><script src='a.js'></script><script src='b.js'></script></head>"
        "<body><div id='root'></div><noscript>You need to enable JavaScript to run this app.</noscript></body></html>"
    )
    assert shell.js_required and shell.text == ""
    assert not extract(f"<html><body><p>{BODY}</p></body></html>").js_required
    # A short page with no scripts is simply short.
    assert not extract("<html><body><p>Hi.</p></body></html>").js_required


def test_plain_text_takes_the_same_cut():
    page = extract(DOC, "text")
    assert page.scope == "main" and "Retries" in page.text and "Pricing" not in page.text


def test_the_old_whole_page_helpers_still_mean_the_whole_page():
    assert "Pricing" in _html_to_markdown(DOC)
    assert _html_to_markdown("<div>" * 600 + "x").startswith("[HTML content omitted")
